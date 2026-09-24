# From https://github.com/langchain-ai/langgraph/blob/23961cff61a42b52525f3b20b4094d8d2fba1744/docs/docs/tutorials/customer-support/customer-support.ipynb
import logging
import os
from typing import Any
import uuid

from maida import trace
from maida.integrations import LangChainCallbackHandler

logging.basicConfig(level=os.environ.get("LOGLEVEL", "ERROR").upper())


def get_api_keys():
    from _customer_support.prereq import _set_env

    _set_env("ANTHROPIC_API_KEY")
    _set_env("OPENAI_API_KEY")
    _set_env("TAVILY_API_KEY")


def get_db(local_file: str | None = None):
    from _customer_support.database import download_db, update_dates

    local_file = download_db(local_file)
    return update_dates(local_file)


def example_questions():
    # Let's create an example conversation a user might have with the assistant
    return [
        "Hi there, what time is my flight?",
        "Am i allowed to update my flight to something sooner? I want to leave later today.",
        "Update my flight to sometime next week then",
        "The next available option is great",
        "what about lodging and transportation?",
        "Yeah i think i'd like an affordable hotel for my week-long stay (7 days). And I'll want to rent a car.",
        "OK could you place a reservation for your recommended hotel? It sounds nice.",
        "yes go ahead and book anything that's moderate expense and has availability.",
        "Now for a car, what are my options?",
        "Awesome let's just get the cheapest option. Go ahead and book for 7 days",
        "Cool so now what recommendations do you have on excursions?",
        "Are they available while I'm there?",
        "interesting - i like the museums, what options are there? ",
        "OK great pick one and book it for my second day there.",
    ]


def get_config(thread_id: str, handler: Any):
    config = {
        "configurable": {
            # The passenger_id is used in our flight tools to
            # fetch the user's flight information
            "passenger_id": "3442 587242",
            # Checkpoints are accessed by thread_id
            "thread_id": thread_id,
        }
    }
    if handler is not None:
        config["callbacks"] = [handler]
    return config


@trace(name="langchain customer support example")
def run_graph(graph, questions: list[str], config: dict):
    from _customer_support.utilities import _print_event

    log = logging.getLogger(__name__)
    log.info("Running graph with %d question(s)", len(questions))
    _printed = set()
    for i, question in enumerate(questions):
        log.info(
            "Question %d: %s",
            i + 1,
            question[:60] + "..." if len(question) > 60 else question,
        )
        events = graph.stream({"messages": ("user", question)}, config, stream_mode="values")
        for event in events:
            _print_event(event, _printed)


def main():
    log = logging.getLogger(__name__)
    get_api_keys()
    db = get_db()
    log.info("DB ready: %s", db)

    # Inject db path so all tool modules use the same DB
    from _customer_support import flights, hotels, car_rentals, excursions

    flights.db = db
    hotels.db = db
    car_rentals.db = db
    excursions.db = db

    from _customer_support.agent import make_chat_model
    from _customer_support.graph import make_graph

    make_chat_model(db)
    graph = make_graph()

    questions = example_questions()
    thread_id = str(uuid.uuid4())

    handler = LangChainCallbackHandler()
    config = get_config(thread_id, handler)
    run_graph(graph, questions, config)


if __name__ == "__main__":
    main()
