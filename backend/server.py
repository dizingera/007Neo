from fastapi import FastAPI, APIRouter, File, UploadFile, HTTPException, Form, Depends
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
from datetime import datetime
import shutil
import mimetypes
import hashlib

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create uploads directory
UPLOAD_DIR = ROOT_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# Simple admin password (in production, use proper JWT)
ADMIN_PASSWORD = "admin123"  # Change this!

# Create the main app
app = FastAPI(title="MagoApp API")
api_router = APIRouter(prefix="/api")

# Security
security = HTTPBearer()

# Models
class MediaFile(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    filename: str
    original_filename: str
    file_path: str
    file_size: int
    mime_type: str
    upload_date: datetime = Field(default_factory=datetime.utcnow)
    file_hash: str

class MediaFileResponse(BaseModel):
    id: str
    filename: str
    original_filename: str
    file_size: int
    mime_type: str
    upload_date: datetime
    download_url: str
    preview_url: Optional[str] = None

class AdminLogin(BaseModel):
    password: str

class AdminResponse(BaseModel):
    access_token: str
    message: str

# Helper functions
def verify_admin_password(password: str) -> bool:
    return password == ADMIN_PASSWORD

def get_file_hash(file_path: Path) -> str:
    """Generate hash for file deduplication"""
    hash_sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()

def is_image(mime_type: str) -> bool:
    return mime_type.startswith('image/')

def is_video(mime_type: str) -> bool:
    return mime_type.startswith('video/')

# Admin Authentication
async def verify_admin_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials.credentials != "admin-token":
        raise HTTPException(status_code=401, detail="Invalid admin token")
    return True

# Routes
@api_router.post("/admin/login", response_model=AdminResponse)
async def admin_login(login_data: AdminLogin):
    if not verify_admin_password(login_data.password):
        raise HTTPException(status_code=401, detail="Invalid password")
    
    return AdminResponse(
        access_token="admin-token",
        message="Login successful"
    )

@api_router.post("/admin/upload", response_model=MediaFileResponse)
async def upload_file(
    file: UploadFile = File(...),
    admin_verified: bool = Depends(verify_admin_token)
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    
    # Create unique filename to avoid conflicts
    file_id = str(uuid.uuid4())
    file_extension = Path(file.filename).suffix.lower()
    unique_filename = f"{file_id}{file_extension}"
    file_path = UPLOAD_DIR / unique_filename
    
    # Save file
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving file: {str(e)}")
    
    # Get file info
    file_size = file_path.stat().st_size
    mime_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    file_hash = get_file_hash(file_path)
    
    # Check for duplicates
    existing_file = await db.media_files.find_one({"file_hash": file_hash})
    if existing_file:
        # Remove the duplicate file we just saved
        file_path.unlink()
        return MediaFileResponse(
            id=existing_file["id"],
            filename=existing_file["filename"],
            original_filename=existing_file["original_filename"],
            file_size=existing_file["file_size"],
            mime_type=existing_file["mime_type"],
            upload_date=existing_file["upload_date"],
            download_url=f"/api/media/download/{existing_file['id']}",
            preview_url=f"/api/media/preview/{existing_file['id']}" if is_image(existing_file["mime_type"]) else None
        )
    
    # Create media file record
    media_file = MediaFile(
        id=file_id,
        filename=unique_filename,
        original_filename=file.filename,
        file_path=str(file_path),
        file_size=file_size,
        mime_type=mime_type,
        file_hash=file_hash
    )
    
    # Save to database
    await db.media_files.insert_one(media_file.dict())
    
    return MediaFileResponse(
        id=media_file.id,
        filename=media_file.filename,
        original_filename=media_file.original_filename,
        file_size=media_file.file_size,
        mime_type=media_file.mime_type,
        upload_date=media_file.upload_date,
        download_url=f"/api/media/download/{media_file.id}",
        preview_url=f"/api/media/preview/{media_file.id}" if is_image(mime_type) else None
    )

@api_router.get("/media/files", response_model=List[MediaFileResponse])
async def get_all_media_files():
    """Public endpoint to get all media files"""
    media_files = await db.media_files.find().sort("upload_date", -1).to_list(1000)
    
    response_files = []
    for media_file in media_files:
        response_files.append(MediaFileResponse(
            id=media_file["id"],
            filename=media_file["filename"],
            original_filename=media_file["original_filename"],
            file_size=media_file["file_size"],
            mime_type=media_file["mime_type"],
            upload_date=media_file["upload_date"],
            download_url=f"/api/media/download/{media_file['id']}",
            preview_url=f"/api/media/preview/{media_file['id']}" if is_image(media_file["mime_type"]) else None
        ))
    
    return response_files

@api_router.get("/media/download/{file_id}")
async def download_file(file_id: str):
    media_file = await db.media_files.find_one({"id": file_id})
    if not media_file:
        raise HTTPException(status_code=404, detail="File not found")
    
    file_path = Path(media_file["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    
    return FileResponse(
        path=str(file_path),
        filename=media_file["original_filename"],
        media_type=media_file["mime_type"]
    )

@api_router.get("/media/preview/{file_id}")
async def preview_file(file_id: str):
    media_file = await db.media_files.find_one({"id": file_id})
    if not media_file:
        raise HTTPException(status_code=404, detail="File not found")
    
    file_path = Path(media_file["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    
    return FileResponse(
        path=str(file_path),
        media_type=media_file["mime_type"]
    )

@api_router.delete("/admin/media/{file_id}")
async def delete_file(file_id: str, admin_verified: bool = Depends(verify_admin_token)):
    media_file = await db.media_files.find_one({"id": file_id})
    if not media_file:
        raise HTTPException(status_code=404, detail="File not found")
    
    # Delete from filesystem
    file_path = Path(media_file["file_path"])
    if file_path.exists():
        file_path.unlink()
    
    # Delete from database
    await db.media_files.delete_one({"id": file_id})
    
    return {"message": "File deleted successfully"}

@api_router.get("/")
async def root():
    return {"message": "Media Gallery API", "version": "1.0"}

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()