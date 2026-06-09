"""Valida documentos generados contra los XSD OFICIALES del SII.

Se salta automáticamente si los esquemas no están en schemas/ (no se versionan;
ver schemas/README.md). Cubre los documentos que no requieren CAF para armarse.
"""

import datetime as dt
from pathlib import Path

import pytest

from dte_chile import exchange as ix
from dte_chile.book import BookCover, BookLine, build_book
from dte_chile.validation import Validator

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"

pytestmark = pytest.mark.skipif(
    not (SCHEMAS / "dte" / "DTE_v10.xsd").exists(),
    reason="XSD del SII no presentes en schemas/ (ver schemas/README.md)",
)

_ENVELOPE = ix.ReceivedEnvelope(
    envelope_name="DTE_test.xml",
    set_dte_id="SetDoc",
    digest="QUJDMTIz",
    issuer_rut="76158145-7",
    receiver_rut="77777777-7",
    documents=[
        ix.ReceivedDocument(33, 3027, dt.date(2026, 6, 8), "76158145-7", "77777777-7", 564060)
    ],
)
_TS = dt.datetime(2026, 6, 8, 11, 0, 0)


def test_receipt_acknowledgment_valid_xsd(cert):
    Validator(SCHEMAS).validate(ix.serialize(ix.build_receipt_acknowledgment(_ENVELOPE, cert, _TS)))


def test_result_valid_xsd(cert):
    Validator(SCHEMAS).validate(ix.serialize(ix.build_result_response(_ENVELOPE, cert, _TS)))


def test_receipts_envelope_valid_xsd(cert):
    Validator(SCHEMAS).validate(
        ix.serialize(ix.build_receipts_envelope(_ENVELOPE, cert, _TS, location="Bodega"))
    )


def test_book_valid_xsd(cert):
    cover = BookCover(
        issuer_rut="76158145-7",
        sender_rut="77777777-7",
        period="2026-06",
        lines=[
            BookLine(
                33,
                3027,
                dt.date(2026, 6, 8),
                "17099910-K",
                "Cliente",
                net_amount=474000,
                vat_amount=90060,
                total_amount=564060,
            )
        ],
    )
    Validator(SCHEMAS).validate(ix.serialize(build_book(cover, cert, _TS)))
