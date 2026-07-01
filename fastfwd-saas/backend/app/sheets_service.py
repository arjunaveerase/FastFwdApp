import re
import pandas as pd
from googleapiclient.discovery import build

def extract_spreadsheet_id(sheet_url: str) -> str:
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", sheet_url)
    if not m:
        raise ValueError("Invalid Google Sheet URL")
    return m.group(1)

def get_sheets_service(credentials):
    return build("sheets", "v4", credentials=credentials)

def get_spreadsheet_meta(credentials, spreadsheet_id: str):
    service = get_sheets_service(credentials)
    return service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()

def list_tabs(credentials, spreadsheet_id: str):
    meta = get_spreadsheet_meta(credentials, spreadsheet_id)
    return [s["properties"]["title"] for s in meta.get("sheets", [])]

def read_tab_as_df(credentials, spreadsheet_id: str, tab_name: str):
    service = get_sheets_service(credentials)
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'!A:AZ"
    ).execute()

    values = result.get("values", [])
    if not values:
        return pd.DataFrame()

    headers = values[0]
    rows = values[1:]
    normalized = []
    for r in rows:
        padded = r + [""] * (len(headers) - len(r))
        normalized.append(padded[:len(headers)])

    return pd.DataFrame(normalized, columns=headers)

def load_sheet_tabs_as_dataframes(credentials, spreadsheet_id: str):
    """
    OPTIMIZED ENGINE: Uses batchGet to download every single tab 
    in a single network request, slashing load times drastically.
    """
    service = get_sheets_service(credentials)
    
    # 1. Fetch metadata to get tab names
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
    
    if not tabs:
        return {}

    # 2. Batch request all tabs at once
    ranges = [f"'{tab}'!A:AZ" for tab in tabs]
    result = service.spreadsheets().values().batchGet(
        spreadsheetId=spreadsheet_id,
        ranges=ranges
    ).execute()

    value_ranges = result.get("valueRanges", [])
    
    tab_dict = {}
    for i, tab in enumerate(tabs):
        values = value_ranges[i].get("values", []) if i < len(value_ranges) else []
        if not values:
            tab_dict[tab] = pd.DataFrame()
            continue
            
        headers = values[0]
        rows = values[1:]
        normalized = []
        for r in rows:
            padded = r + [""] * (len(headers) - len(r))
            normalized.append(padded[:len(headers)])
            
        tab_dict[tab] = pd.DataFrame(normalized, columns=headers)
        
    return tab_dict