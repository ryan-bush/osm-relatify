from dataclasses import dataclass
from typing import Literal

from models.element_id import ElementId

# 'auto' leaves the role to the route: entry only at the first stop, exit only at the
# last, and whatever the relation already had in between
StopRole = Literal['auto', 'both', 'entry_only', 'exit_only']


@dataclass(frozen=True, kw_only=True, slots=True)
class StopPlanEntry:
    """One visit to a stop, in the order the mapper arranged the route's stops.

    A stop the bus serves twice is in the plan twice, once for each visit.
    """

    id: ElementId
    role: StopRole = 'auto'
