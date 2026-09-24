import logging
from datetime import datetime

from langchain_anthropic import ChatAnthropic
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableConfig

from .car_rentals import (
    book_car_rental,
    cancel_car_rental,
    search_car_rentals,
    update_car_rental,
)
from .company_policies import init_policies, lookup_policy
from .excursions import (
    book_excursion,
    cancel_excursion,
    search_trip_recommendations,
    update_excursion,
)
from .flights import (
    cancel_ticket,
    fetch_user_flight_information,
    search_flights,
    update_ticket_to_new_flight,
)
from .hotels import book_hotel, cancel_hotel, search_hotels, update_hotel
from .state import State

logger = logging.getLogger(__name__)


class Assistant:
    def __init__(self, runnable: Runnable):
        self.runnable = runnable

    def __call__(self, state: State, config: RunnableConfig):
        logger.info("Assistant responding")
        while True:
            configuration = config.get("configurable", {})
            passenger_id = configuration.get("passenger_id", None)
            state = {**state, "user_info": passenger_id}
            result = self.runnable.invoke(state)
            # If the LLM happens to return an empty response, we will re-prompt it
            # for an actual response.
            if not result.tool_calls and (
                not result.content or isinstance(result.content, list) and not result.content[0].get("text")
            ):
                messages = state["messages"] + [("user", "Respond with a real output.")]
                state = {**state, "messages": messages}
            else:
                break
        return {"messages": result}


# Set by make_chat_model() for graph to import.
part_1_tools: list = []
part_1_assistant_runnable: Runnable | None = None


def make_chat_model(db: str) -> None:
    global part_1_tools, part_1_assistant_runnable
    init_policies()
    # Haiku is faster and cheaper, but less accurate
    llm = ChatAnthropic(model="claude-3-haiku-20240307")
    # llm = ChatAnthropic(model="claude-3-sonnet-20240229", temperature=1)
    # You could swap LLMs, though you will likely want to update the prompts when
    # doing so!
    # from langchain_openai import ChatOpenAI

    # llm = ChatOpenAI(model="gpt-4-turbo-preview")

    primary_assistant_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a helpful customer support assistant for Swiss Airlines. "
                " Use the provided tools to search for flights, company policies, and other information to assist the user's queries. "
                " When searching, be persistent. Expand your query bounds if the first search returns no results. "
                " If a search comes up empty, expand your search before giving up."
                "\n\nCurrent user:\n<User>\n{user_info}\n</User>"
                "\nCurrent time: {time}.",
            ),
            ("placeholder", "{messages}"),
        ]
    ).partial(time=datetime.now)

    part_1_tools = [
        TavilySearchResults(max_results=1),
        fetch_user_flight_information,
        search_flights,
        lookup_policy,
        update_ticket_to_new_flight,
        cancel_ticket,
        search_car_rentals,
        book_car_rental,
        update_car_rental,
        cancel_car_rental,
        search_hotels,
        book_hotel,
        update_hotel,
        cancel_hotel,
        search_trip_recommendations,
        book_excursion,
        update_excursion,
        cancel_excursion,
    ]
    part_1_assistant_runnable = primary_assistant_prompt | llm.bind_tools(part_1_tools)
