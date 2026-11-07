import asyncio
import sys

# Add /app to python path
sys.path.insert(0, "/app")

from src.core.database import AsyncSessionFactory
from src.data.entities.models import User, UserRole, Source, BiasLean
from src.services.auth_service import hash_password
from sqlalchemy import select

async def seed_all():
    print("==================================================")
    print("Starting database seeding...")
    print("==================================================")
    
    async with AsyncSessionFactory() as session:
        # 1. Seed Admin User
        email = "admin@globelens.ai"
        password = "adminpass123"
        name = "Global Admin"
        
        res_user = await session.execute(select(User).where(User.email == email))
        user = res_user.scalars().first()
        
        if user:
            print(f"User {email} already exists. Updating role to ADMIN...")
            user.role = UserRole.ADMIN
            user.password_hash = hash_password(password)
        else:
            print(f"Creating Admin user ({email})...")
            admin = User(
                name=name,
                email=email,
                password_hash=hash_password(password),
                role=UserRole.ADMIN
            )
            session.add(admin)
            
        # 2. Seed Default Media Sources
        default_sources = [
            {
                "name": "BBC News",
                "url": "https://www.bbc.com/news/world",
                "country": "United Kingdom",
                "credibility_score": 0.85,
                "bias_lean": BiasLean.CENTER
            },
            {
                "name": "CNN",
                "url": "https://www.cnn.com",
                "country": "United States",
                "credibility_score": 0.70,
                "bias_lean": BiasLean.CENTER_LEFT
            },
            {
                "name": "Al Jazeera",
                "url": "https://www.aljazeera.com",
                "country": "Qatar",
                "credibility_score": 0.75,
                "bias_lean": BiasLean.CENTER
            }
        ]
        
        for src_data in default_sources:
            res_src = await session.execute(select(Source).where(Source.name == src_data["name"]))
            existing_src = res_src.scalars().first()
            if existing_src:
                print(f"Source '{src_data['name']}' already exists. Updating...")
                existing_src.url = src_data["url"]
                existing_src.country = src_data["country"]
                existing_src.credibility_score = src_data["credibility_score"]
                existing_src.bias_lean = src_data["bias_lean"]
            else:
                print(f"Creating Source '{src_data['name']}'...")
                new_src = Source(
                    name=src_data["name"],
                    url=src_data["url"],
                    country=src_data["country"],
                    credibility_score=src_data["credibility_score"],
                    bias_lean=src_data["bias_lean"]
                )
                session.add(new_src)
                
        await session.commit()
        print("\n==================================================")
        print("✅ Database seeding completed successfully!")
        print("==================================================")

if __name__ == "__main__":
    asyncio.run(seed_all())
