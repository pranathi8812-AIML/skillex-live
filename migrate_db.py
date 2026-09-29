from app import app, db
from models import ServiceRequest, ServiceOffer

with app.app_context():
    db.create_all()
    print("New tables successfully created in Neon!")