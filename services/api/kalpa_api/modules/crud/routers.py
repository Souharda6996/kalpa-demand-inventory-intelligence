"""Concrete master-data routers built from the generic factory."""

from fastapi import APIRouter

from kalpa_api.db.models import (
    Location,
    Product,
    Promotion,
    Supplier,
    SupplierProduct,
)
from kalpa_api.modules.crud import schemas as sc
from kalpa_api.modules.crud.generic import build_crud

crud_routers: list[APIRouter] = [
    build_crud(
        prefix="/locations",
        tag="catalog",
        model=Location,
        create_schema=sc.LocationIn,
        update_schema=sc.LocationPatch,
        out_schema=sc.LocationOut,
        name="locations",
        sortable={"name", "city", "type", "created_at"},
        filterable={"type", "city", "state"},
    ),
    build_crud(
        prefix="/suppliers",
        tag="catalog",
        model=Supplier,
        create_schema=sc.SupplierIn,
        update_schema=sc.SupplierPatch,
        out_schema=sc.SupplierOut,
        name="suppliers",
        sortable={"name", "default_lead_time_days", "created_at"},
        filterable={"name"},
    ),
    build_crud(
        prefix="/products",
        tag="catalog",
        model=Product,
        create_schema=sc.ProductIn,
        update_schema=sc.ProductPatch,
        out_schema=sc.ProductOut,
        name="products",
        sortable={"sku", "name", "category", "created_at"},
        filterable={"category", "dept", "sku"},
    ),
    build_crud(
        prefix="/supplier-products",
        tag="catalog",
        model=SupplierProduct,
        create_schema=sc.SupplierProductIn,
        update_schema=sc.SupplierProductPatch,
        out_schema=sc.SupplierProductOut,
        name="supplier_products",
        sortable={"unit_cost", "lead_time_days", "created_at"},
        filterable={"supplier_id", "product_id"},
        fk_checks={"supplier_id": Supplier, "product_id": Product},
    ),
    build_crud(
        prefix="/promotions",
        tag="catalog",
        model=Promotion,
        create_schema=sc.PromotionIn,
        update_schema=sc.PromotionPatch,
        out_schema=sc.PromotionOut,
        name="promotions",
        sortable={"start_date", "end_date", "discount_pct", "created_at"},
        filterable={"product_id", "location_id", "category"},
        fk_checks={"product_id": Product, "location_id": Location},
    ),
]
# Inventory has custom read scoping for store managers - see inventory.py.
INVENTORY_SCHEMAS = (sc.InventoryIn, sc.InventoryPatch, sc.InventoryOut)
