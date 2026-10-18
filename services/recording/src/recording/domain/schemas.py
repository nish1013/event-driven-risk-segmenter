import re
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints
from pydantic.alias_generators import to_camel

NonEmpty = Annotated[str, StringConstraints(min_length=1)]
DecimalString = Annotated[str, StringConstraints(pattern=r"^[0-9]+(\.[0-9]+)?$")]
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
ISO_DURATION = re.compile(
    r"P(?=[0-9T])([0-9]+Y)?([0-9]+M)?([0-9]+W)?([0-9]+D)?(T(?=[0-9])([0-9]+H)?([0-9]+M)?([0-9]+S)?)?"
)


def _iso_duration(value: str) -> str:
    if not ISO_DURATION.fullmatch(value):
        raise ValueError("must be an ISO 8601 duration such as P7D or PT1H")
    return value


IsoDuration = Annotated[str, AfterValidator(_iso_duration)]

ACTIVITY_TYPES = ("chargeback", "velocity_breach", "kyc_expired", "failed_login")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, alias_generator=to_camel)


class ChargebackPayload(StrictModel):
    transaction_id: NonEmpty
    amount: DecimalString
    currency: CurrencyCode
    reason_code: NonEmpty


class VelocityBreachPayload(StrictModel):
    metric: Literal["txn_count", "txn_amount"]
    window: IsoDuration
    observed: DecimalString
    limit: DecimalString


class KycExpiredPayload(StrictModel):
    kyc_case_id: NonEmpty
    document_type: Literal["identity", "business_registration", "proof_of_address"]


class FailedLoginPayload(StrictModel):
    user_id: NonEmpty
    method: Literal["password", "otp"]


class NewActivityFields(StrictModel):
    activity_id: NonEmpty
    merchant_id: NonEmpty
    occurred_at: AwareDatetime


class NewChargeback(NewActivityFields):
    type: Literal["chargeback"]
    payload: ChargebackPayload


class NewVelocityBreach(NewActivityFields):
    type: Literal["velocity_breach"]
    payload: VelocityBreachPayload


class NewKycExpired(NewActivityFields):
    type: Literal["kyc_expired"]
    payload: KycExpiredPayload


class NewFailedLogin(NewActivityFields):
    type: Literal["failed_login"]
    payload: FailedLoginPayload


AnyNewActivity = NewChargeback | NewVelocityBreach | NewKycExpired | NewFailedLogin
NewActivity = Annotated[AnyNewActivity, Field(discriminator="type")]
