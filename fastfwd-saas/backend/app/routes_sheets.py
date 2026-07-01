from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User, OAuthAccount, SheetConnection, ColumnMapping
from app.schemas import SheetConnectRequest, SelectTabRequest, MappingRequest
from app.google_oauth import build_credentials_from_tokens
from app.sheets_service import extract_spreadsheet_id, get_spreadsheet_meta, list_tabs, read_tab_as_df

router = APIRouter(prefix="/sheets", tags=["sheets"])


def get_user_and_oauth(db: Session, user_email: str):
    user = db.query(User).filter(User.email == user_email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found. Please sign in again.")

    oauth = db.query(OAuthAccount).filter(OAuthAccount.user_id == user.id).first()
    if not oauth:
        raise HTTPException(status_code=400, detail="Google account not connected. Please sign in again.")

    return user, oauth


@router.post("/connect")
def connect_sheet(payload: SheetConnectRequest, db: Session = Depends(get_db)):
    try:
        user, oauth = get_user_and_oauth(db, payload.user_email)
        creds = build_credentials_from_tokens(oauth.access_token, oauth.refresh_token)

        spreadsheet_id = extract_spreadsheet_id(payload.spreadsheet_url)
        if not spreadsheet_id:
            raise HTTPException(status_code=400, detail="Invalid Google Sheet URL.")

        meta = get_spreadsheet_meta(creds, spreadsheet_id)
        title = meta.get("properties", {}).get("title", "") or "Untitled Sheet"

        tabs = list_tabs(creds, spreadsheet_id)

        existing = db.query(SheetConnection).filter(
            SheetConnection.user_email == payload.user_email,
            SheetConnection.sheet_id == spreadsheet_id
        ).first()

        if existing:
            existing.sheet_name = title
            db.commit()
            db.refresh(existing)
            row = existing
        else:
            row = SheetConnection(
                user_email=payload.user_email,
                sheet_id=spreadsheet_id,
                sheet_name=title,
                sender_name="Arjun SE",
            )
            db.add(row)
            db.commit()
            db.refresh(row)
        return {
            "connection_id": row.id,
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_name": title,
            "tabs": tabs,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Could not access Google Sheet. Technical detail: {str(e)}",
        )

@router.post("/select-tab")
def select_tab(payload: SelectTabRequest, db: Session = Depends(get_db)):
    conn = db.query(SheetConnection).filter(SheetConnection.id == payload.connection_id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Sheet connection not found")
    conn.sheet_name = payload.selected_tab
    db.commit()
    return {"ok": True}


@router.get("/{connection_id}/columns")
def get_columns(connection_id: int, user_email: str, db: Session = Depends(get_db)):
    user, oauth = get_user_and_oauth(db, user_email)
    conn = db.query(SheetConnection).filter(
        SheetConnection.id == connection_id,
        SheetConnection.user_email == user_email
    ).first()

    if not conn:
        raise HTTPException(status_code=404, detail="Sheet connection not found")
    if not conn.sheet_name:
        raise HTTPException(status_code=400, detail="No tab selected")

    creds = build_credentials_from_tokens(oauth.access_token, oauth.refresh_token)
    df = read_tab_as_df(creds, conn.sheet_id, conn.sheet_name)

    return {
        "columns": list(df.columns) if not df.empty else []
    }


@router.post("/mapping")
def save_mapping(payload: MappingRequest, db: Session = Depends(get_db)):
    existing = db.query(ColumnMapping).filter(
        ColumnMapping.user_email == payload.vendor_name_col
    ).first()

    if existing:
        row = existing
    else:
        row = ColumnMapping(user_email=payload.vendor_name_col)
        db.add(row)

    row.vendor_column = payload.vendor_name_col
    row.recipient_column = payload.to_email_col
    row.quantity_column = payload.template_type_col

    db.commit()
    return {"ok": True}