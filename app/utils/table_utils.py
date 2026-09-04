from typing import List
from app.api.schemas.document import TableInfo
import uuid

def create_table_info(page_number: int, headers: List[str], rows: List[List[str]], table_id: str = None) -> TableInfo:
    """
    Create a TableInfo object with required fields
    """
    if table_id is None:
        table_id = str(uuid.uuid4())
    
    # Ensure all cells are strings
    headers = [str(h) if h is not None else '' for h in headers]
    rows = [[str(cell) if cell is not None else '' for cell in row] for row in rows]
    
    return TableInfo(
        table_id=table_id,
        page_number=page_number,
        headers=headers,
        rows=rows
    )