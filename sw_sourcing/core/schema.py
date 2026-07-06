"""The common listing shape every adapter normalizes into.

Nothing downstream of an adapter may see raw source data — only `Listing`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, HttpUrl

BuyingOption = Literal["auction", "fixed_price", "best_offer"]


class Listing(BaseModel):
    source: str
    listing_id: str
    url: HttpUrl
    title: str
    description: str
    price: float
    shipping: float
    buying_option: BuyingOption
    offers_accepted: bool
    returns_accepted: bool
    seller_feedback: float
    location: str
    images: list[HttpUrl]
    fetched_at: datetime
