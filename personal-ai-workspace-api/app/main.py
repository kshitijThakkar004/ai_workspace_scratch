from typing import Literal
from fastapi import FastAPI
from pydantic import BaseModel
import logging
from .core.logging import configure_logging

class HealthResponse(BaseModel):
    status: Literal["ok"]

def create_app()->FastAPI:
    configure_logging("INFO")
    application=FastAPI(
        title="Personal AI Workspace",
        version= "0.1.0"
    )
    logger=logging.info("Started Application")
    @application.get(
        "/health",
        response_model=HealthResponse,
        tags=['system'],
        )
    def health()->HealthResponse:
        logging.debug("Health Check Requested")
        return HealthResponse(status="ok")

    
    @application.get("/practice/greet")

    def greet(name:str):
        logging.info("Greetings requested")
        return {"message":f"Hello {name}"}
    return application

    

app=create_app()