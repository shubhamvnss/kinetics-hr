from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DB_HOST: str = Field(
        default="",
        validation_alias=AliasChoices("DB_HOST", "PGHOST12"),
    )
    DB_PORT: int = Field(
        default=5432,
        validation_alias=AliasChoices("DB_PORT", "PGPORT12"),
    )
    DB_NAME: str = Field(
        default="",
        validation_alias=AliasChoices("DB_NAME", "PGDATABASE12"),
    )
    DB_USER: str = Field(
        default="",
        validation_alias=AliasChoices("DB_USER", "PGUSER12"),
    )
    DB_PASSWORD: str = Field(
        default="",
        validation_alias=AliasChoices("DB_PASSWORD", "PGPASSWORD12"),
    )
    DB_SCHEMA: str = Field(
        default="corporate_loan",
        validation_alias=AliasChoices("DB_SCHEMA", "PGSCHEMA12"),
    )

    DB_SSLMODE: str = Field(
        default="require",
        validation_alias=AliasChoices("DB_SSLMODE", "PGSSLMODE12"),
    )
    DB_CONNECT_TIMEOUT: int = Field(
        default=15,
        validation_alias=AliasChoices("DB_CONNECT_TIMEOUT", "PGCONNECT_TIMEOUT12"),
    )
    AZURE_OPENAI_ENDPOINT: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AZURE_OPENAI_ENDPOINT", "OPENAI_ENDPOINT"),
    )
    AZURE_OPENAI_API_KEY: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AZURE_OPENAI_API_KEY", "OPENAI_KEY"),
    )
    AZURE_OPENAI_DEPLOYMENT: str = Field(
        default="gpt-5.1",
        validation_alias=AliasChoices("AZURE_OPENAI_DEPLOYMENT", "OPENAI_DEPLOYMENT"),
    )
    AZURE_OPENAI_API_VERSION: str = Field(
        default="2024-12-01-preview",
        validation_alias=AliasChoices("AZURE_OPENAI_API_VERSION", "OPENAI_API_VERSION"),
    )
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SMTP_USERNAME", "SMTP_USER"),
    )
    SMTP_PASSWORD: str | None = None
    SMTP_FROM: str = Field(
        default="kinetic-noreply@example.com",
        validation_alias=AliasChoices("SMTP_FROM", "MAIL_FROM"),
    )
    APPROVAL_RECIPIENTS: str = ""
    APPROVAL_BASE_URL: str = (
        "https://kinetic-corporate-loan-cueqftgucxgnc9bv.southindia-01.azurewebsites.net"
    )
    CACHE_VERSION: int = Field(
        default=4,
        validation_alias="CACHE_VERSION",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()