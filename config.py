import os
from dotenv import load_dotenv

# Load variables from the .env file
load_dotenv()

class Config:
    # Use the secret key from .env, or a fallback if not found
    SECRET_KEY = os.environ.get('SECRET_KEY', 'default-secret-key')
    
    # Get the database URL
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')
    
    # Fix for SQLAlchemy 1.4+ when using Postgres (Vercel/Neon deployment later)
    if SQLALCHEMY_DATABASE_URI and SQLALCHEMY_DATABASE_URI.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace("postgres://", "postgresql://", 1)
        
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    