from typing import Literal
from fastapi import FastAPI
from pydantic import BaseModel

class HealthResponse(BaseModel):
    status: Literal["ok"]

def create_app()->FastAPI:
    application=FastAPI(
        title="Personal AI Workspace",
        version= "0.1.0"
    )

    @application.get(
        "/health",
        response_model=HealthResponse,
        tags=['system'],
        )
    def health()->HealthResponse:
        return HealthResponse(status="ok")
    
    @application.get("/practice/greet")

    def greet(name:str):
        return {"message":f"Hello {name}"}
    return application

    

app=create_app()