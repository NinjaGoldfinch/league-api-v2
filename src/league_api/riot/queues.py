from enum import StrEnum


class LeagueQueue(StrEnum):
    RANKED_SOLO_5X5 = "RANKED_SOLO_5x5"
    RANKED_FLEX_SR = "RANKED_FLEX_SR"
    RANKED_FLEX_TT = "RANKED_FLEX_TT"


def league_queue_label(queue: str | LeagueQueue) -> str:
    queue_value = queue.value if isinstance(queue, LeagueQueue) else queue
    queue_labels = {
        LeagueQueue.RANKED_SOLO_5X5.value: "Ranked Solo/Duo",
        LeagueQueue.RANKED_FLEX_SR.value: "Ranked Flex",
        LeagueQueue.RANKED_FLEX_TT.value: "Ranked Flex 3v3",
    }
    return queue_labels.get(queue_value, queue_value)
