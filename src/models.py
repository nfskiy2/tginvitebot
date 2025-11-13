from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(Integer, unique=True, index=True)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    invite_links = relationship("InviteLink", back_populates="inviter")
    invitations_sent = relationship("InvitationLog", foreign_keys="[InvitationLog.inviter_id]", back_populates="inviter")
    invitations_received = relationship("InvitationLog", foreign_keys="[InvitationLog.invitee_id]", back_populates="invitee")

class InviteLink(Base):
    __tablename__ = "invite_links"

    id = Column(Integer, primary_key=True, index=True)
    link = Column(String, unique=True, index=True)
    inviter_id = Column(Integer, ForeignKey("users.id"))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)
    used_at = Column(DateTime(timezone=True), nullable=True)

    inviter = relationship("User", back_populates="invite_links")

class InvitationLog(Base):
    __tablename__ = "invitation_logs"

    id = Column(Integer, primary_key=True, index=True)
    inviter_id = Column(Integer, ForeignKey("users.id"))
    invitee_id = Column(Integer, ForeignKey("users.id"))
    invite_link_id = Column(Integer, ForeignKey("invite_links.id"), nullable=True)
    invited_at = Column(DateTime(timezone=True), server_default=func.now())

    inviter = relationship("User", foreign_keys="[InvitationLog.inviter_id]", back_populates="invitations_sent")
    invitee = relationship("User", foreign_keys="[InvitationLog.invitee_id]", back_populates="invitations_received")
    invite_link = relationship("InviteLink")
