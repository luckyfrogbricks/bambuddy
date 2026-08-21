import enum
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class SpoolCodeKind(enum.StrEnum):
    """The two vocabularies a spool code can belong to.

    ``classify_code`` in ``backend/app/schemas/spool.py`` produces these values
    (as plain strings, so the schema layer stays free of ORM imports); a unit
    test locks the two vocabularies together.
    """

    GTIN = "gtin"
    SKU = "sku"


class SpoolCode(Base):
    """A single code (GTIN barcode or manufacturer SKU/article number) associated with a spool.

    A spool can have several: the code actually scanned/typed (``is_primary``),
    plus siblings discovered by cross-referencing the Open Filament Database
    and SpoolmanDB-Community (other package-size GTINs, the refill-pack GTIN,
    the manufacturer SKU/article number) — see
    ``backend/app/services/barcode_resolver.py``. ``Spool.barcode`` itself stays
    the single denormalized "primary code" column so existing CSV/table/form
    behavior is untouched; this table is the additive one-to-many store.
    """

    __tablename__ = "spool_code"

    id: Mapped[int] = mapped_column(primary_key=True)
    spool_id: Mapped[int] = mapped_column(ForeignKey("spool.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # a SpoolCodeKind value
    is_refill: Mapped[bool] = mapped_column(Boolean, default=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    spool: Mapped["Spool"] = relationship(back_populates="codes")

    __table_args__ = (
        UniqueConstraint("spool_id", "code", name="uq_spool_code_spool_id_code"),
        # The whole table is new in this feature, so every install (SQLite and
        # Postgres alike) gets the constraint straight from create_all() — no
        # dialect-specific ALTER migration is ever needed for it.
        CheckConstraint(
            "kind IN ({})".format(", ".join(f"'{k.value}'" for k in SpoolCodeKind)),
            name="ck_spool_code_kind",
        ),
    )


from backend.app.models.spool import Spool  # noqa: E402, F401
