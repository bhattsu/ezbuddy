"""
Session Configuration Management

This module handles session-based configuration for RAG operations,
allowing users to select vector stores and RAG strategies per session.
"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum
import uuid


class VectorStoreType(str, Enum):
    """Supported vector store types."""
    PGVECTOR = "pgvector"


class RAGStrategyType(str, Enum):
    """Supported RAG strategy types."""
    NAIVE = "naive"


class RetrievalConfig(BaseModel):
    """Configuration for retrieval operations."""
    top_k: int = Field(default=10, ge=1, le=100, description="Number of documents to retrieve")
    score_threshold: float = Field(default=0.0, ge=0.0, le=1.0, description="Minimum similarity score")
    rerank: bool = Field(default=False, description="Whether to use reranking")
    hybrid: bool = Field(default=False, description="Whether to use hybrid search")
    filters: Optional[Dict[str, Any]] = Field(default=None, description="Metadata filters")


class GenerationConfig(BaseModel):
    """Configuration for LLM generation."""
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2000, ge=1, le=100000)
    system_prompt: Optional[str] = None


class SessionConfig(BaseModel):
    """
    Complete session configuration for RAG operations.
    
    This configuration is stored per chat session and determines
    which vector store and RAG strategy to use.
    """
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    vector_store: VectorStoreType = Field(default=VectorStoreType.PGVECTOR)
    rag_type: RAGStrategyType = Field(default=RAGStrategyType.NAIVE)
    embedding_model: str = Field(default="bedrock-titan")
    collection_name: str = Field(default="default")
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        use_enum_values = True


class ConversationMessage(BaseModel):
    """A single message in a conversation."""
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ConversationHistory(BaseModel):
    """Conversation history for conversational RAG."""
    session_id: str
    messages: List[ConversationMessage] = Field(default_factory=list)
    max_messages: int = Field(default=20, description="Maximum messages to retain")

    def add_message(self, role: str, content: str, metadata: Dict[str, Any] = None):
        """Add a message to the conversation history."""
        message = ConversationMessage(
            role=role,
            content=content,
            metadata=metadata or {}
        )
        self.messages.append(message)
        
        # Trim old messages if exceeding max
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]

    def get_context_window(self, window_size: int = 5) -> List[ConversationMessage]:
        """Get the last N messages for context."""
        return self.messages[-window_size:]

    def clear(self):
        """Clear conversation history."""
        self.messages = []


class SessionManager:
    """
    Manages session configurations.
    
    In production, this should be backed by a database (DynamoDB, PostgreSQL, etc.).
    This implementation uses in-memory storage for simplicity.
    """
    
    _sessions: Dict[str, SessionConfig] = {}
    _conversations: Dict[str, ConversationHistory] = {}

    @classmethod
    def create_session(
        cls,
        vector_store: VectorStoreType = VectorStoreType.PGVECTOR,
        rag_type: RAGStrategyType = RAGStrategyType.NAIVE,
        **kwargs
    ) -> SessionConfig:
        """
        Create a new session with the specified configuration.
        
        Args:
            vector_store: The vector store to use.
            rag_type: The RAG strategy to use.
            **kwargs: Additional session configuration.
            
        Returns:
            The created SessionConfig.
        """
        session = SessionConfig(
            vector_store=vector_store,
            rag_type=rag_type,
            **kwargs
        )
        cls._sessions[session.session_id] = session
        return session

    @classmethod
    def get_session(cls, session_id: str) -> Optional[SessionConfig]:
        """Get a session by ID."""
        return cls._sessions.get(session_id)

    @classmethod
    def update_session(
        cls,
        session_id: str,
        **updates
    ) -> Optional[SessionConfig]:
        """
        Update an existing session.
        
        Args:
            session_id: The session to update.
            **updates: Fields to update.
            
        Returns:
            The updated session, or None if not found.
        """
        session = cls._sessions.get(session_id)
        if session:
            for key, value in updates.items():
                if hasattr(session, key):
                    setattr(session, key, value)
            cls._sessions[session_id] = session
        return session

    @classmethod
    def delete_session(cls, session_id: str) -> bool:
        """Delete a session by ID."""
        if session_id in cls._sessions:
            del cls._sessions[session_id]
            # Also delete conversation history
            if session_id in cls._conversations:
                del cls._conversations[session_id]
            return True
        return False

    @classmethod
    def get_conversation(cls, session_id: str) -> ConversationHistory:
        """Get or create conversation history for a session."""
        if session_id not in cls._conversations:
            cls._conversations[session_id] = ConversationHistory(session_id=session_id)
        return cls._conversations[session_id]

    @classmethod
    def list_sessions(cls) -> List[str]:
        """List all session IDs."""
        return list(cls._sessions.keys())

    @classmethod
    def clear_all(cls) -> None:
        """Clear all sessions and conversations."""
        cls._sessions.clear()
        cls._conversations.clear()

