"""Фабрика приложения для uvicorn/Docker (--factory)."""
from fastapi import FastAPI

from adapters.http.main import create_app
from app.config import get_settings


def create_app_from_settings() -> FastAPI:
    return create_app(get_settings())
