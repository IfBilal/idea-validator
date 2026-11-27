"""Base for models of what an upstream service returned.

Unknown fields are dropped (an allow-list): a field M2 or M3 adds later never reaches a browser
until it is declared here. Request bodies use the strict ``Message`` base instead.
"""

from pydantic import BaseModel, ConfigDict


class Upstream(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
