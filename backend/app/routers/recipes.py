"""Recipe CRUD — per-domain, versioned. The client applies recipes to the
rendered DOM; the backend only stores this app-level knowledge about sites."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from ..config_store import config_transaction
from ..scraper.models import Recipe
from ..scraper.recipes import RecipeStore

router = APIRouter(prefix="/api")


def _recipes(request: Request) -> RecipeStore:
    return request.app.state.recipes


@router.get("/recipes", response_model=list[str])
def list_recipes(request: Request) -> list[str]:
    return _recipes(request).list_domains()


@router.get("/recipes/{domain}", response_model=Recipe)
def get_recipe(request: Request, domain: str) -> Recipe:
    recipe = _recipes(request).get(domain)
    if recipe is None:
        raise HTTPException(status_code=404, detail="no recipe for that domain")
    return recipe


@router.put("/recipes/{domain}", response_model=Recipe)
def put_recipe(request: Request, domain: str, recipe: Recipe) -> Recipe:
    recipe.domain = domain  # the path is authoritative
    # teaching a site again un-hides it in the Sources list
    with config_transaction() as cfg:
        hidden = set(cfg.get("hidden_sources", []))
        hidden.discard(domain)
        cfg["hidden_sources"] = sorted(hidden)
    return _recipes(request).save(recipe)


@router.delete("/recipes/{domain}", status_code=204)
def delete_recipe(request: Request, domain: str) -> Response:
    """Forget the learned recipe; the domain's titles are untouched."""
    if not _recipes(request).delete(domain):
        raise HTTPException(status_code=404, detail="no recipe for that domain")
    return Response(status_code=204)
