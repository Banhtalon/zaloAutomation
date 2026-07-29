from pydantic import BaseModel
from typing import Optional

class GroupBase(BaseModel):
    group_name: str

class Group(GroupBase):
    id: int
    person_in_charge: Optional[str] = "Chưa xác định"
    class_date: Optional[str] = ""

class IgnoredGroup(GroupBase):
    id: int
