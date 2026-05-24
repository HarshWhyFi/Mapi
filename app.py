from fastapi import FastAPI, HTTPException, Depends, UploadFile, File
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, ValidationError
import sqlite3
import jwt
import os
from datetime import datetime, timedelta
from typing import List, Dict
import logging
import pandas as pd
from io import BytesIO

# ====================== CONFIG ======================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Crunchy Bite Cafe Menu API",
    version="2.1",
    description="FastAPI + SQLite Menu Management"
)

DB_FILE = "data.db"
SECRET_KEY = "crunchybite-super-secret-key-2026-change-me"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# Admin Credentials (CHANGE THESE!)
ADMIN_EMAIL = "admin@crunchybite.com"
ADMIN_PASSWORD = "admin123"

security = HTTPBearer()

# ====================== DATABASE ======================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS menu (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            Category TEXT NOT NULL,
            Name TEXT NOT NULL,
            Description TEXT NOT NULL,
            Price TEXT NOT NULL,
            Tags TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()
    logger.info(f"✅ Database ready: {DB_FILE}")

init_db()

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# ====================== MODELS ======================
class MenuItem(BaseModel):
    Category: str
    Name: str
    Description: str
    Price: str
    Tags: str

class LoginRequest(BaseModel):
    email: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

class BulkUploadResponse(BaseModel):
    message: str
    added_count: int
    total_items: int
    errors: List[str] = []

# ====================== JWT ======================
def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        return jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
    except:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

# ====================== ROUTES ======================

@app.post("/login", response_model=Token)
async def login(login_data: LoginRequest):
    if login_data.email == ADMIN_EMAIL and login_data.password == ADMIN_PASSWORD:
        token = create_access_token({"sub": login_data.email})
        return {"access_token": token, "token_type": "bearer"}
    raise HTTPException(status_code=401, detail="Invalid credentials")


@app.get("/menu")
async def get_all_menu():
    conn = get_db()
    items = conn.execute("SELECT * FROM menu").fetchall()
    conn.close()
    return [dict(row) for row in items]


@app.get("/menu/category/{category}")
async def get_by_category(category: str):
    conn = get_db()
    items = conn.execute("SELECT * FROM menu WHERE LOWER(Category) = LOWER(?)", (category,)).fetchall()
    conn.close()
    return [dict(row) for row in items]


@app.get("/menu/search")
async def search_menu(q: str):
    if not q:
        return []
    conn = get_db()
    query = f"%{q.lower()}%"
    items = conn.execute("""
        SELECT * FROM menu 
        WHERE LOWER(Category) LIKE ? OR LOWER(Name) LIKE ? 
        OR LOWER(Description) LIKE ? OR LOWER(Price) LIKE ? OR LOWER(Tags) LIKE ?
    """, (query, query, query, query, query)).fetchall()
    conn.close()
    return [dict(row) for row in items]


@app.post("/menu")
async def add_menu_item(item: MenuItem, token=Depends(verify_token)):
    conn = get_db()
    conn.execute("""
        INSERT INTO menu (Category, Name, Description, Price, Tags)
        VALUES (?, ?, ?, ?, ?)
    """, (item.Category, item.Name, item.Description, item.Price, item.Tags))
    conn.commit()
    conn.close()
    return {"message": "Item added successfully", "item": item.dict()}


@app.put("/menu/{item_id}")
async def update_menu_item(item_id: int, item: MenuItem, token=Depends(verify_token)):
    conn = get_db()
    cursor = conn.execute("""
        UPDATE menu 
        SET Category=?, Name=?, Description=?, Price=?, Tags=?
        WHERE id=?
    """, (item.Category, item.Name, item.Description, item.Price, item.Tags, item_id))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Item not found")
    
    conn.commit()
    conn.close()
    return {"message": "Item updated successfully"}


@app.delete("/menu/{item_id}")
async def delete_menu_item(item_id: int, token=Depends(verify_token)):
    conn = get_db()
    cursor = conn.execute("DELETE FROM menu WHERE id=?", (item_id,))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Item not found")
    
    conn.commit()
    conn.close()
    return {"message": "Item deleted successfully"}


@app.post("/menu/bulk", response_model=BulkUploadResponse)
async def bulk_upload(csv_file: UploadFile = File(...), token=Depends(verify_token)):
    if not csv_file.filename.endswith('.csv'):
        raise HTTPException(400, "Only CSV files allowed")
    
    contents = await csv_file.read()
    added = 0
    errors = []
    
    try:
        df = pd.read_csv(BytesIO(contents))
        required = ['Category', 'Name', 'Description', 'Price', 'Tags']
        if not all(col in df.columns for col in required):
            raise HTTPException(400, "Missing required columns")
        
        conn = get_db()
        for _, row in df.iterrows():
            try:
                MenuItem(**row.to_dict())
                conn.execute("""
                    INSERT INTO menu (Category, Name, Description, Price, Tags)
                    VALUES (?, ?, ?, ?, ?)
                """, (row['Category'], row['Name'], row['Description'], row['Price'], row['Tags']))
                added += 1
            except Exception as e:
                errors.append(f"Row {_+1}: {str(e)}")
        
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM menu").fetchone()[0]
        conn.close()
        
    except Exception as e:
        raise HTTPException(500, f"Bulk upload failed: {str(e)}")
    
    return {
        "message": f"Successfully added {added} items",
        "added_count": added,
        "total_items": total,
        "errors": errors[:10]
    }


@app.get("/health")
async def health_check():
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM menu").fetchone()[0]
    conn.close()
    return {"status": "healthy", "items": count, "database": DB_FILE}


# One-time import from your old CSV (Run once if needed)
@app.post("/import-csv")
async def import_from_csv(token=Depends(verify_token)):
    csv_path = "crunchy_bite_menu_converted_1779342562724.csv"
    if not os.path.exists(csv_path):
        raise HTTPException(404, "CSV file not found")
    
    df = pd.read_csv(csv_path)
    conn = get_db()
    added = 0
    for _, row in df.iterrows():
        try:
            conn.execute("""
                INSERT INTO menu (Category, Name, Description, Price, Tags)
                VALUES (?, ?, ?, ?, ?)
            """, (row['Category'], row['Name'], row['Description'], row['Price'], row['Tags']))
            added += 1
        except:
            pass
    conn.commit()
    conn.close()
    return {"message": f"Imported {added} items from CSV"}