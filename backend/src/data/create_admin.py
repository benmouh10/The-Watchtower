import asyncio
import sys

# Add /app to python path
sys.path.insert(0, "/app")

from src.core.database import AsyncSessionFactory
from src.data.entities.models import User, UserRole
from src.services.auth_service import hash_password
from sqlalchemy import select

async def create_admin():
    email = "admin@globelens.ai"
    password = "adminpass123"
    name = "Global Admin"
    
    print(f"Creating Admin user inside database...")
    async with AsyncSessionFactory() as session:
        # Check if user already exists
        res = await session.execute(select(User).where(User.email == email))
        user = res.scalars().first()
        
        if user:
            print(f"User with email {email} already exists. Upgrading to ADMIN role and updating password...")
            user.role = UserRole.ADMIN
            user.password_hash = hash_password(password)
            user.name = name
            await session.commit()
            print("✅ Admin user updated successfully.")
        else:
            admin_user = User(
                name=name,
                email=email,
                password_hash=hash_password(password),
                role=UserRole.ADMIN
            )
            session.add(admin_user)
            await session.commit()
            print("✅ Admin user created successfully.")
            
        print("\nAdmin Credentials:")
        print(f"Email: {email}")
        print(f"Password: {password}")

if __name__ == "__main__":
    asyncio.run(create_admin())
