"""Shared SRS image-response shape literal for openapi ledger batch 6d.

The 5-key set emitted by ``srs_images.py::_item_response`` — a static literal
(no conditional keys), served bare by PUT /items/{id}/image, PUT
/items/{id}/image/upload, and DELETE /items/{id}/image.
"""

IMAGE_ITEM_KEYS = {"id", "text", "translation", "card_type", "image_url"}
