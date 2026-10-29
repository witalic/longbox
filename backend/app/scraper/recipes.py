"""Per-domain recipe storage — IN THE VAULT (`<root>/recipes.json`).

What longbox learned about the sites a library was built from belongs to that
library: carrying the vault to another machine carries its sites with it. One
document keyed by domain, each recipe versioned on its own. The page-capture
filter rides along, because it is the same kind of knowledge — what a reader's
page looks like.
"""
from __future__ import annotations

from pydantic import ValidationError

from ..library.vault import UnreadableDocument, Vault
from .models import PageFilter, Recipe, domain_key

DOC = "recipes.json"
SCHEMA = 1  # the shape this build writes; ANY change to it bumps this (Vault.doc_transaction)


def _parse(domain: str, raw: object) -> Recipe:
    """A stored recipe — or UnreadableDocument for one this build cannot read,
    which must then be neither applied nor saved over."""
    try:
        return Recipe.model_validate(raw)
    except ValidationError as e:
        raise UnreadableDocument(
            f"the recipe for {domain} in {DOC} is in a shape this build cannot read") from e


def _usable(recipe: Recipe) -> bool:
    # A pre-candidate (prototype-era) recipe parses but can extract nothing —
    # it reads as unlearned rather than as a recipe that silently no-ops.
    return (recipe.chapters is not None or bool(recipe.hidden)
            or any(f.candidates for f in recipe.fields.values()))


def _recipes_of(doc: dict) -> dict:
    recipes = doc.setdefault("recipes", {})
    if not isinstance(recipes, dict):
        raise UnreadableDocument(f"{DOC} holds recipes in a shape this build cannot read")
    return recipes


class RecipeStore:
    def __init__(self, vault: Vault) -> None:
        self.vault = vault

    def _recipes(self) -> dict:
        return _recipes_of(self.vault.read_doc(DOC))

    def get(self, domain: str) -> Recipe | None:
        raw = self._recipes().get(domain_key(domain))
        if raw is None:
            return None
        recipe = _parse(domain, raw)
        return recipe if _usable(recipe) else None

    def catalog(self) -> tuple[set[str], dict[str, Recipe]]:
        """Every domain with a stored recipe, and the recipes among them that can
        be applied — from ONE read, which is what a listing joins. One this build
        cannot read is listed but not applied, and saving over it is refused."""
        stored = self._recipes()
        usable: dict[str, Recipe] = {}
        for key, raw in stored.items():
            try:
                recipe = _parse(key, raw)
            except UnreadableDocument:
                continue
            if _usable(recipe):
                usable[key] = recipe
        return set(stored), usable

    def list_domains(self) -> list[str]:
        return sorted(self._recipes())

    def save(self, recipe: Recipe) -> Recipe:
        """Persist a recipe, bumping the version past any existing one for that domain."""
        key = domain_key(recipe.domain)
        with self.vault.doc_transaction(DOC, schema=SCHEMA) as doc:
            recipes = _recipes_of(doc)
            existing = _parse(key, recipes[key]) if key in recipes else None
            usable = existing is not None and _usable(existing)
            recipe.version = existing.version + 1 if usable else max(recipe.version, 1)
            recipes[key] = recipe.model_dump()
        return recipe

    def adopt(self, recipes: list[Recipe]) -> int:
        """Take recipes learned elsewhere, versions and all. A domain this vault
        already knows keeps its own. Returns how many were taken."""
        have = self._recipes()
        if all(domain_key(r.domain) in have for r in recipes):
            return 0
        taken = 0
        with self.vault.doc_transaction(DOC, schema=SCHEMA) as doc:
            mine = _recipes_of(doc)
            for recipe in recipes:
                key = domain_key(recipe.domain)
                if key not in mine:
                    mine[key] = recipe.model_dump()
                    taken += 1
        return taken

    def delete(self, domain: str) -> bool:
        """Forget everything learned about a domain."""
        key = domain_key(domain)
        if key not in self._recipes():
            return False
        with self.vault.doc_transaction(DOC, schema=SCHEMA) as doc:
            return _recipes_of(doc).pop(key, None) is not None

    def page_filter(self) -> PageFilter:
        try:
            return PageFilter.model_validate(self.vault.read_doc(DOC).get("pageFilter") or {})
        except ValidationError:
            return PageFilter()

    def set_page_filter(self, value: PageFilter) -> PageFilter:
        with self.vault.doc_transaction(DOC, schema=SCHEMA) as doc:
            doc["pageFilter"] = value.model_dump()
        return value
