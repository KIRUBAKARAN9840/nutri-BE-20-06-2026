from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, Boolean, JSON,
)
from sqlalchemy.orm import relationship
from app.models.database import Base
from datetime import datetime


class Post(Base):
    __tablename__ = "posts"

    post_id = Column(Integer, primary_key=True, index=True)
    gym_id = Column(Integer, nullable=False, index=True)

    client_id = Column(Integer, nullable=True, index=True)
    content = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    is_pinned = Column(Boolean, default=False)
    status=Column(String(45))

    comments = relationship("Comment", back_populates="post", cascade="all, delete-orphan")
    likes = relationship("Like", back_populates="post", cascade="all, delete-orphan")
    media = relationship("PostMedia", back_populates="post", cascade="all, delete-orphan")


class PostMedia(Base):
    __tablename__ = "post_media"

    media_id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.post_id", ondelete="CASCADE"), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_type = Column(String(50), nullable=False)
    file_path = Column(String(500), nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    status=Column(String(45))

    post = relationship("Post", back_populates="media")


class Comment(Base):
    __tablename__ = "comments"

    comment_id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.post_id", ondelete="CASCADE"), nullable=False)
    gym_id = Column(Integer, nullable=False, index=True)
    client_id = Column(Integer, nullable=True, index=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    post = relationship("Post", back_populates="comments")


class Like(Base):
    __tablename__ = "likes"

    like_id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.post_id", ondelete="CASCADE"), nullable=False)
    gym_id = Column(Integer, nullable=False, index=True)
    client_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.now)

    post = relationship("Post", back_populates="likes")


class Report(Base):
    __tablename__ ="report"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    user_role = Column(String(20), nullable=False)
    reported_id = Column(Integer, nullable=False)
    reported_role = Column(String(20), nullable=False)
    post_id = Column(Integer, nullable=False)
    reason = Column(Text, nullable=False)
    post_content = Column(Text, nullable=False)
    status = Column( Boolean, nullable=False)


class BlockedUsers(Base):
    __tablename__ = 'blocked_users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    user_role= Column(String(45),nullable=False)
    blocked_user_id = Column(JSON, nullable=False)


class FeedInterest(Base):
    """
    Tracks whether a client has seen the feed interest/referral modal.
    When client opens Feed tab:
    - If no row exists: show modal, create row with feed_interest=0
    - If row exists with feed_interest=0: show modal
    - If row exists with feed_interest=1: don't show modal
    """
    __tablename__ = "feed_interest"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    feed_interest = Column(Integer, default=0, nullable=False)  # 0 = show modal, 1 = don't show
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
