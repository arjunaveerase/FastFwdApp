import time
from io import BytesIO
from typing import Dict, List

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.gmail_service import get_message_thread_context, send_email
from app.google_oauth import load_user_credentials
from app.models import User, OAuthAccount, SheetConnection, SendLog
from app.sheets_service import load_sheet_tabs_as_dataframes
from app.template_service import normalize_template_type, render_template

router = APIRouter(prefix="/workflows", tags=["workflows"])


EMAILER_REQUIRED = [
    "vendor_name",
    "template_type",
    "sub_line",
    "to_email_ids",
    "cc_email_ids",
    "thread_id",
    "message_id",
]

SKU_REQUIRED = [
    "vendor_name",
    "warehouse_name",
    "sales_last_30_days",
    "current_stock",
    "safety_stock_level",
]


def split_email_string(value: str) -> List[str]:
    if not value:
        return []
    parts = []
    for item in str(value).replace("\n", ";").replace(",", ";").split(";"):
        item = item.strip()
        if item:
            parts.append(item)
    deduped = []
    seen = set()
    for item in parts:
        low = item.lower()
        if low not in seen:
            seen.add(low)
            deduped.append(item)
    return deduped


def join_email_list(values: List[str]) -> str:
    return "; ".join(values or [])


# GLOBAL CACHE: Stores the downloaded Google Sheet in RAM for 60 seconds 
# so batch sending takes 3 seconds instead of 90 seconds.
_TABS_CACHE = {}

def get_sheet_frames(user_email: str, connection_id: int, db: Session):
    connection = (
        db.query(SheetConnection)
        .filter(
            SheetConnection.id == connection_id,
            SheetConnection.user_email == user_email,
        )
        .first()
    )
    if not connection:
        raise HTTPException(status_code=404, detail="Sheet connection not found")

    creds = load_user_credentials(user_email)
    
    if not creds:
        user = db.query(User).filter(User.email == user_email).first()
        if user:
            oauth = db.query(OAuthAccount).filter(OAuthAccount.user_id == user.id).first()
            if oauth:
                from app.google_oauth import build_credentials_from_tokens
                creds = build_credentials_from_tokens(oauth.access_token, oauth.refresh_token)

    if not creds:
        raise HTTPException(
            status_code=401, 
            detail="Google credentials not found. Please log out and sign in again."
        )

    # Check the high-speed RAM cache before downloading the sheet again
    cache_key = connection.sheet_id
    cached = _TABS_CACHE.get(cache_key)
    
    if cached and (time.time() - cached['timestamp'] < 60):
        tabs = cached['data']
    else:
        tabs = load_sheet_tabs_as_dataframes(creds, connection.sheet_id)
        _TABS_CACHE[cache_key] = {'data': tabs, 'timestamp': time.time()}

    return connection, tabs, creds


def normalize_emailer_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=EMAILER_REQUIRED)

    normalized = df.copy()
    normalized.columns = [str(c).strip() for c in normalized.columns]
    for col in EMAILER_REQUIRED:
        if col not in normalized.columns:
            normalized[col] = ""
    return normalized.fillna("")


def normalize_sku_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=SKU_REQUIRED)

    normalized = df.copy()
    normalized.columns = [str(c).strip() for c in normalized.columns]
    for col in SKU_REQUIRED:
        if col not in normalized.columns:
            normalized[col] = 0 if col != "vendor_name" and col != "warehouse_name" else ""
    return normalized.fillna(0)


def get_emailer_and_sku_frames(tabs: Dict[str, pd.DataFrame]):
    if not tabs:
        raise HTTPException(status_code=400, detail="No tabs found in the sheet")

    emailer_tab = None
    sku_tab = None
    for name in tabs.keys():
        lower = name.lower().strip()
        if lower == "emailer":
            emailer_tab = name
        if lower == "sku_data":
            sku_tab = name

    if not emailer_tab:
        for name in tabs.keys():
            if "email" in name.lower():
                emailer_tab = name
                break
    if not sku_tab:
        for name in tabs.keys():
            if "sku" in name.lower():
                sku_tab = name
                break

    if not emailer_tab or not sku_tab:
        raise HTTPException(
            status_code=400,
            detail="Could not detect both Emailer and SKU_Data tabs",
        )

    emailer_df = normalize_emailer_df(tabs[emailer_tab])
    sku_df = normalize_sku_df(tabs[sku_tab])
    return emailer_tab, sku_tab, emailer_df, sku_df


def get_sheet_vendor_rows(emailer_df: pd.DataFrame, vendor_name: str) -> pd.DataFrame:
    return emailer_df[emailer_df["vendor_name"].astype(str).str.strip() == vendor_name].copy()


def get_latest_sheet_context(emailer_df: pd.DataFrame, vendor_name: str):
    rows = get_sheet_vendor_rows(emailer_df, vendor_name)
    if rows.empty:
        return None

    thread_rows = rows[
        (rows["thread_id"].astype(str).str.strip() != "")
        | (rows["message_id"].astype(str).str.strip() != "")
    ]
    target_rows = thread_rows if not thread_rows.empty else rows
    row = target_rows.iloc[-1]
    return {
        "vendor_name": vendor_name,
        "template_type": normalize_template_type(str(row.get("template_type", ""))),
        "subject": str(row.get("sub_line", "")).strip(),
        "to_emails": split_email_string(str(row.get("to_email_ids", ""))),
        "cc_emails": split_email_string(str(row.get("cc_email_ids", ""))),
        "thread_id": str(row.get("thread_id", "")).strip(),
        "message_id": str(row.get("message_id", "")).strip(),
        "source": "sheet",
    }


def get_latest_log_context(db: Session, vendor_name: str):
    row = (
        db.query(SendLog)
        .filter(
            SendLog.vendor_name == vendor_name,
            SendLog.send_status == "sent",
        )
        .order_by(SendLog.id.desc())
        .first()
    )
    if not row:
        return None

    return {
        "vendor_name": vendor_name,
        "template_type": normalize_template_type(row.template_type),
        "subject": (row.subject or "").strip(),
        "to_emails": split_email_string(row.to_emails or ""), 
        "cc_emails": split_email_string(row.cc_emails or ""),
        "thread_id": (row.gmail_thread_id or "").strip(), 
        "message_id": (row.gmail_message_id or "").strip(), 
        "source": "db",
    }


def get_previous_context(db: Session, emailer_df: pd.DataFrame, vendor_name: str):
    db_ctx = get_latest_log_context(db, vendor_name)
    if db_ctx and (db_ctx.get("thread_id") or db_ctx.get("message_id")):
        return db_ctx

    sheet_ctx = get_latest_sheet_context(emailer_df, vendor_name)
    if sheet_ctx and (sheet_ctx.get("thread_id") or sheet_ctx.get("message_id")):
        return sheet_ctx

    return db_ctx or sheet_ctx


def get_next_template_for_vendor(db: Session, emailer_df: pd.DataFrame, vendor_name: str):
    history = []

    sheet_rows = get_sheet_vendor_rows(emailer_df, vendor_name)
    for _, row in sheet_rows.iterrows():
        history.append(normalize_template_type(str(row.get("template_type", ""))))

    db_logs = (
        db.query(SendLog)
        .filter(
            SendLog.vendor_name == vendor_name,
            SendLog.send_status == "sent",
        )
        .order_by(SendLog.id.asc())
        .all()
    )
    for log in db_logs:
        history.append(normalize_template_type(log.template_type))

    if "RO_FINAL" in history:
        return "RO_FINAL"
    if "RO_FOLLOWUP" in history:
        return "RO_FINAL"
    if "RO_INITIAL" in history:
        return "RO_FOLLOWUP"
    return "RO_INITIAL"


def compute_row_totals(vendor_df: pd.DataFrame):
    local = vendor_df.copy()
    for numeric_col in [
        "sales_last_30_days",
        "current_stock",
        "safety_stock_level",
    ]:
        local[numeric_col] = pd.to_numeric(local[numeric_col], errors="coerce").fillna(0)

    local["suggested_reorder"] = (local["sales_last_30_days"] * 0.15).round().astype(int)
    
    # FIXED: Extract total current stock directly for the table row injection
    return {
        "total_units_sold": int(local["sales_last_30_days"].sum()),
        "total_current_stock": int(local["current_stock"].sum()),
        "total_suggested_reorder": int(local["suggested_reorder"].sum()),
        "warehouse_summary": (
            local.groupby("warehouse_name", dropna=False, as_index=False)
            .agg(
                {
                    "sales_last_30_days": "sum",
                    "current_stock": "sum",
                    "suggested_reorder": "sum",
                }
            )
            .sort_values(by="sales_last_30_days", ascending=False)
            .to_dict(orient="records")
        ),
        "detail_df": local,
    }


def build_context(vendor_name: str, vendor_df: pd.DataFrame, sender_name: str):
    totals = compute_row_totals(vendor_df)

    warehouse_rows = []
    for row in totals["warehouse_summary"]:
        warehouse_rows.append(
            {
                "warehouse": str(row.get("warehouse_name", "")).strip() or "Unknown Warehouse",
                "sales": int(row.get("sales_last_30_days", 0)),
                "current_stock": int(row.get("current_stock", 0)),
                "suggested_reorder": int(row.get("suggested_reorder", 0)),
            }
        )

    warehouse_table_html = "".join(
        f"<tr>"
        f"<td style='padding:8px;border:1px solid #b9c2d0'>{row['warehouse']}</td>"
        f"<td style='padding:8px;border:1px solid #b9c2d0;text-align:right'>{row['sales']:,}</td>"
        f"<td style='padding:8px;border:1px solid #b9c2d0;text-align:right'>{row['current_stock']:,}</td>"
        f"<td style='padding:8px;border:1px solid #b9c2d0;text-align:right'>{row['suggested_reorder']:,}</td>"
        f"</tr>"
        for row in warehouse_rows
    )

    # FIXED: Inject the shaded, bolded Total row at the very bottom of the table
    warehouse_table_html += (
        f"<tr style='font-weight:bold;background-color:#f1f3f5'>"
        f"<td style='padding:8px;border:1px solid #b9c2d0'>Total</td>"
        f"<td style='padding:8px;border:1px solid #b9c2d0;text-align:right'>{totals['total_units_sold']:,}</td>"
        f"<td style='padding:8px;border:1px solid #b9c2d0;text-align:right'>{totals['total_current_stock']:,}</td>"
        f"<td style='padding:8px;border:1px solid #b9c2d0;text-align:right'>{totals['total_suggested_reorder']:,}</td>"
        f"</tr>"
    )

    return {
        "vendor_name": vendor_name,
        "date": pd.Timestamp.today().strftime("%d-%m-%Y"),
        "total_units_sold": f"{totals['total_units_sold']:,}",
        "total_suggested_reorder": f"{totals['total_suggested_reorder']:,}",
        "warehouse_rows": warehouse_rows,
        "warehouse_table_html": warehouse_table_html,
        "sender_name": sender_name,
        "detail_df": totals["detail_df"],
    }


def get_vendor_data(sku_df: pd.DataFrame, vendor_name: str):
    rows = sku_df[sku_df["vendor_name"].astype(str).str.strip() == vendor_name].copy()
    if rows.empty:
        raise HTTPException(status_code=404, detail="No matching SKU data for vendor_name")
    return rows


def create_vendor_attachment(vendor_name: str, vendor_df: pd.DataFrame):
    output = BytesIO()
    export_cols = [
        c
        for c in [
            "vendor_name",
            "brand_name",
            "category",
            "sub_category",
            "sku_code",
            "warehouse_name",
            "sales_last_30_days",
            "current_stock",
            "safety_stock_level",
        ]
        if c in vendor_df.columns
    ]

    attachment_df = vendor_df[export_cols].copy()
    attachment_df["suggested_reorder"] = (
        pd.to_numeric(attachment_df.get("sales_last_30_days", 0), errors="coerce")
        .fillna(0)
        .mul(0.15)
        .round()
        .astype(int)
    )

    attachment_df.to_csv(output, index=False)
    output.seek(0)
    safe_name = vendor_name.replace(" ", "_").replace("/", "_")
    return f"{safe_name}_reorder_detail.csv", output.read()


def build_vendor_defaults(db: Session, emailer_df: pd.DataFrame, vendors: List[str]):
    defaults = {}
    for vendor in vendors:
        prev = get_previous_context(db, emailer_df, vendor)
        next_template = get_next_template_for_vendor(db, emailer_df, vendor)

        defaults[vendor] = {
            "next_template": next_template,
            "default_to": join_email_list(prev.get("to_emails", [])) if prev else "",
            "default_cc": join_email_list(prev.get("cc_emails", [])) if prev else "",
            "default_subject": (prev.get("subject", "") if prev else "").strip(),
            "has_previous_thread": bool(prev and (prev.get("thread_id") or prev.get("message_id"))),
            "thread_id": prev.get("thread_id", "") if prev else "",
            "message_id": prev.get("message_id", "") if prev else "",
        }
    return defaults


def get_effective_template_and_thread(db: Session, emailer_df: pd.DataFrame, vendor_name: str, requested_template: str):
    previous = get_previous_context(db, emailer_df, vendor_name)
    requested = normalize_template_type(requested_template)

    if requested == "SS" or not requested or requested.strip() == "":
        requested = "RO_INITIAL"

    if requested in {"RO_FOLLOWUP", "RO_FINAL"}:
        if previous and (previous.get("thread_id") or previous.get("message_id")):
            return requested, previous, True
        return "RO_INITIAL", previous, False

    return "RO_INITIAL", previous, False


@router.get("/bootstrap")
def bootstrap_workflow(user_email: str, connection_id: int, db: Session = Depends(get_db)):
    connection, tabs, _ = get_sheet_frames(user_email, connection_id, db)
    emailer_tab, sku_tab, emailer_df, sku_df = get_emailer_and_sku_frames(tabs)

    vendors = sorted(
        {
            str(v).strip()
            for v in sku_df.get("vendor_name", pd.Series(dtype=str)).tolist()
            if str(v).strip()
        }
    )

    rows = emailer_df[EMAILER_REQUIRED].fillna("").to_dict(orient="records")
    vendor_defaults = build_vendor_defaults(db, emailer_df, vendors)

    return {
        "spreadsheet_name": connection.sheet_name,
        "tabs": list(tabs.keys()),
        "detected": {
            "emailer_tab": emailer_tab,
            "sku_tab": sku_tab,
        },
        "columns": emailer_df.columns.tolist(),
        "sku_columns": sku_df.columns.tolist(),
        "vendors": vendors,
        "rows": rows,
        "vendor_defaults": vendor_defaults,
    }


@router.post("/preview")
def preview_workflow(payload: dict, db: Session = Depends(get_db)):
    user_email = payload.get("user_email") or payload.get("email") or "arjunaveerase@gmail.com"
    connection_id = int(payload.get("connection_id", 1))
    vendor_name = payload.get("vendor_name", "Unknown Vendor")
    sender_name = payload.get("sender_name", "Arjun SE")
    
    temp_type = payload.get("template_type") or payload.get("template") or "RO_INITIAL"
    if temp_type not in ["RO_INITIAL", "RO_FOLLOWUP", "RO_FINAL"]:
        temp_type = "RO_INITIAL"

    _, tabs, _ = get_sheet_frames(user_email, connection_id, db)
    _, _, emailer_df, sku_df = get_emailer_and_sku_frames(tabs)

    effective_template, previous, using_previous_thread = get_effective_template_and_thread(
        db,
        emailer_df,
        vendor_name,
        temp_type,
    )

    vendor_df = get_vendor_data(sku_df, vendor_name)
    context = build_context(vendor_name, vendor_df, sender_name)
    subject, html_body = render_template(effective_template, context)

    if using_previous_thread and previous and previous.get("subject"):
        subject = previous["subject"]

    attachment_name, _ = create_vendor_attachment(vendor_name, context["detail_df"])

    return {
        "subject": subject,
        "html_body": html_body,
        "attachment_name": attachment_name,
        "summary": {
            "vendor_name": vendor_name,
            "date": context["date"],
            "total_units_sold": context["total_units_sold"],
            "total_suggested_reorder": context["total_suggested_reorder"],
            "sender_name": sender_name,
        },
        "effective_template": effective_template,
        "using_previous_thread": using_previous_thread,
    }


@router.post("/send")
def send_workflow(payload: dict, db: Session = Depends(get_db)):
    user_email = payload.get("user_email") or payload.get("email") or "arjunaveerase@gmail.com"
    connection_id = int(payload.get("connection_id", 1))
    vendor_name = payload.get("vendor_name", "Unknown Vendor")
    sender_name = payload.get("sender_name", "Arjun SE")
    
    temp_type = payload.get("template_type") or payload.get("template") or "RO_INITIAL"
    if temp_type not in ["RO_INITIAL", "RO_FOLLOWUP", "RO_FINAL"]:
        temp_type = "RO_INITIAL"

    _, tabs, creds = get_sheet_frames(user_email, connection_id, db)
    _, _, emailer_df, sku_df = get_emailer_and_sku_frames(tabs)

    raw_to = payload.get("to_emails") or payload.get("to") or ""
    raw_cc = payload.get("cc_emails") or payload.get("cc") or ""
    to_list = raw_to if isinstance(raw_to, list) else split_email_string(raw_to)
    cc_list = raw_cc if isinstance(raw_cc, list) else split_email_string(raw_cc)

    if not to_list:
        to_list = [user_email]

    vendor_df = get_vendor_data(sku_df, vendor_name)
    context = build_context(vendor_name, vendor_df, sender_name)

    effective_template, previous, using_previous_thread = get_effective_template_and_thread(
        db,
        emailer_df,
        vendor_name,
        temp_type,
    )

    subject, html_body = render_template(effective_template, context)
    if using_previous_thread and previous and previous.get("subject"):
        subject = previous["subject"]

    attachment_name, attachment_bytes = create_vendor_attachment(vendor_name, context["detail_df"])

    thread_id = previous.get("thread_id", "") if previous else ""
    reply_message_id = None
    references_header = None

    if using_previous_thread and previous and previous.get("message_id"):
        try:
            msg_ctx = get_message_thread_context(creds, previous["message_id"])
            if msg_ctx:
                thread_id = msg_ctx.get("thread_id") or thread_id
                reply_message_id = msg_ctx.get("rfc822_message_id") or None
                references_header = msg_ctx.get("references") or None
        except Exception:
            reply_message_id = None
            references_header = None

    sent = send_email(
        credentials=creds,
        to_emails=to_list,
        cc_emails=cc_list,
        subject=subject,
        html_body=html_body,
        attachment_filename=attachment_name,
        attachment_bytes=attachment_bytes,
        thread_id=thread_id or None,
        reply_message_id=reply_message_id,
        references_header=references_header,
        sender_name=sender_name,
        sender_email=user_email,
    )

    log = SendLog(
        user_email=user_email,
        vendor_name=vendor_name,
        template_type=effective_template,
        subject=subject,
        to_emails=join_email_list(to_list), 
        cc_emails=join_email_list(cc_list), 
        gmail_thread_id=sent.get("threadId", ""),
        gmail_message_id=sent.get("id", ""),
        send_status="sent",
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    return {
        "status": "sent",
        "effective_template": effective_template,
        "using_previous_thread": using_previous_thread,
        "subject": subject,
        "thread_id": sent.get("threadId", ""),
        "message_id": sent.get("id", ""),
        "attachment_name": attachment_name,
    }


@router.get("/logs")
def logs_workflow(user_email: str, db: Session = Depends(get_db)):
    logs = (
        db.query(SendLog)
        .order_by(SendLog.id.desc())
        .limit(20)
        .all()
    )

    return {
        "logs": [
            {
                "id": row.id,
                "vendor_name": row.vendor_name,
                "template_type": row.template_type,
                "subject": row.subject,
                "to_emails": row.to_emails, 
                "cc_emails": row.cc_emails,
                "gmail_thread_id": row.gmail_thread_id,
                "gmail_message_id": row.gmail_message_id,
                "send_status": row.send_status,
            }
            for row in logs
        ]
    }