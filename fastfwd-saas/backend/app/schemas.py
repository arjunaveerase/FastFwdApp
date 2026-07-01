from pydantic import BaseModel, Extra
from typing import List, Union, Any, Optional

# -------------------------
# SHEETS
# -------------------------
class SheetConnectRequest(BaseModel):
    user_email: str
    spreadsheet_url: str

class SelectTabRequest(BaseModel):
    connection_id: int
    selected_tab: str

class MappingRequest(BaseModel):
    sheet_connection_id: int
    vendor_name_col: str
    template_type_col: str
    to_email_col: str
    cc_email_col: str
    thread_id_col: str
    message_id_col: str
    subject_col: str
    remarks_col: str

# -------------------------
# WORKFLOW
# -------------------------
class WorkflowPreviewRequest(BaseModel):
    user_email: str
    connection_id: int
    vendor_name: str
    template_type: str
    sender_name: str

class WorkflowSendRequest(BaseModel):
    # Allow absolutely any extra or mismatched field keys to pass through without a 422 error
    class Config:
        extra = Extra.allow

    user_email: Optional[str] = None
    connection_id: Optional[int] = None
    vendor_name: Optional[str] = None
    template_type: Optional[str] = None
    to_emails: Optional[Union[List[str], str]] = None
    cc_emails: Optional[Union[List[str], str]] = None
    sender_name: Optional[str] = None

# -------------------------
# RESPONSES
# -------------------------
class VendorPreview(BaseModel):
    vendor_name: str
    recipient: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None

class GenericResponse(BaseModel):
    success: bool
    message: str

class LogResponse(BaseModel):
    vendor_name: Optional[str] = None
    recipient: Optional[str] = None
    status: Optional[str] = None

class SheetPreviewResponse(BaseModel):
    headers: List[str]
    rows: List[Dict[str, Any]]