from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
)

from sqlalchemy.orm import relationship

from datetime import datetime

from app.db import Base


class User(Base):

    __tablename__ = "users"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    email = Column(
        String,
        unique=True,
        index=True
    )

    name = Column(String)

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    oauth_accounts = relationship(
        "OAuthAccount",
        back_populates="user"
    )


class OAuthAccount(Base):

    __tablename__ = "oauth_accounts"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    user_id = Column(
        Integer,
        ForeignKey("users.id")
    )

    provider = Column(String)

    access_token = Column(Text)

    refresh_token = Column(Text)

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    user = relationship(
        "User",
        back_populates="oauth_accounts"
    )






class SheetConnection(Base):

    __tablename__ = "sheet_connections"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    user_email = Column(String)

    sheet_id = Column(String)

    sheet_name = Column(String)

    sender_name = Column(String)

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )


class ColumnMapping(Base):

    __tablename__ = "column_mappings"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    user_email = Column(String)

    vendor_column = Column(String)

    recipient_column = Column(String)

    quantity_column = Column(String)

    warehouse_column = Column(String)

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

class GoogleSheetConnection(Base):
    __tablename__ = "google_sheet_connections"

    id = Column(Integer, primary_key=True, index=True)
    user_email = Column(String)
    spreadsheet_id = Column(String)
    spreadsheet_url = Column(String)
    spreadsheet_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class SendLog(Base):
    __tablename__ = "send_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_email = Column(String)
    vendor_name = Column(String)
    to_emails = Column(Text)
    cc_emails = Column(Text)
    subject = Column(Text)
    gmail_thread_id = Column(String)
    gmail_message_id = Column(String)
    template_type = Column(String)
    send_status = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)