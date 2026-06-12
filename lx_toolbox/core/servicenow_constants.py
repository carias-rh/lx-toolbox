"""ServiceNow RHT application constants for the HUB instance."""

from enum import Enum

# HUB instance (legacy: https://redhat.service-now.com)
HUB_INSTANCE_URL = "https://redhathub.service-now.com"

# Table names (legacy -> HUB)
RHT_TASK_TABLE = "x_redha_rht_task"
RHT_METRIC_TABLE = "x_redha_rht_metric"
RHT_COURSE_TABLE = "x_redha_rht_course"


def rht_task_field(field_name: str) -> str:
    """Return the ServiceNow form field element id for the RHT task table."""
    return f"{RHT_TASK_TABLE}.{field_name}"


def rht_task_table_url(instance_url: str) -> str:
    """Return the REST API URL for the RHT task table."""
    return f"{instance_url}/api/now/table/{RHT_TASK_TABLE}"


class TicketState(Enum):
    """HUB state values for x_redha_rht_task."""

    NEW = "1"
    IN_PROGRESS = "2"
    PENDING = "3"
    RESOLVED = "6"
    CLOSED = "7"
    CANCELED = "8"

    # Aliases for legacy code paths
    CLOSED_CANCELLED = CANCELED


class PendingReason(Enum):
    """Required when state is Pending (3)."""

    PLATFORM = "Platform"
    IT = "IT"
    EXAMINER_TRAINING = "Examiner Training"
    ENGINEERING = "Engineering"
    CUSTOMER = "Customer"
    CURRICULUM = "Curriculum"


class ClosedReason(Enum):
    """Required when closing a ticket as duplicate."""

    DUPLICATE = "Duplicate"


# States to query for unassigned/active tickets in HUB
DEFAULT_TARGET_STATES = ["1", "2", "3"]
