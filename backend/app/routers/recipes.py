"""Recipe CRUD — per-domain, versioned — and the page-capture filter. The client
applies recipes to the rendered DOM; the backend only stores what the ACTIVE
library knows about its sites."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from ..library.service import Library
from ..scraper.models import PageFilter, Recipe

router = APIRouter(prefix="/api")


def _lib(request: Request) -> Library:
    return request.app.state.library


@router.get("/recipes", response_model=list[str])
def list_recipes(request: Request) -> list[str]:
    return _lib(request).recipes.list_domains()


@router.get("/recipes/{domain}", response_model=Recipe)
def get_recipe(request: Request, domain: str) -> Recipe:
    recipe = _lib(request).recipes.get(domain)
    if recipe is None:
        raise HTTPException(status_code=404, detail="no recipe for that domain")
    return recipe


@router.put("/recipes/{domain}", response_model=Recipe)
def put_recipe(request: Request, domain: str, recipe: Recipe) -> Recipe:
    recipe.domain = domain  # the path is authoritative
    lib = _lib(request)
    saved = lib.recipes.save(recipe)  # first: it is the write that may refuse
    # teaching a site again brings it back into the Sources list
    lib.source_prefs.set_removed(domain, False)
    return saved


@router.delete("/recipes/{domain}", status_code=204)
def delete_recipe(request: Request, domain: str) -> Response:
    """Forget the learned recipe; the domain's titles are untouched."""
    if not _lib(request).recipes.delete(domain):
        raise HTTPException(status_code=404, detail="no recipe for that domain")
    return Response(status_code=204)


@router.get("/page-filter", response_model=PageFilter)
def get_page_filter(request: Request) -> PageFilter:
    return _lib(request).recipes.page_filter()


@router.put("/page-filter", response_model=PageFilter)
def put_page_filter(request: Request, body: PageFilter) -> PageFilter:
    return _lib(request).recipes.set_page_filter(body)
