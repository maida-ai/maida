import re

import numpy as np
import openai
from langchain_core.tools import tool
import requests


import logging

logger = logging.getLogger(__name__)


def get_company_policies():
    logger.info("Getting company policies...")
    response = requests.get("https://storage.googleapis.com/benchmarks-artifacts/travel-db/swiss_faq.md")
    response.raise_for_status()
    faq_text = response.text

    docs = [{"page_content": txt} for txt in re.split(r"(?=\n##)", faq_text)]
    return docs


class VectorStoreRetriever:
    def __init__(self, docs: list, vectors: list, oai_client):
        self._arr = np.array(vectors)
        self._docs = docs
        self._client = oai_client

    @classmethod
    def from_docs(cls, docs, oai_client):
        embeddings = oai_client.embeddings.create(
            model="text-embedding-3-small", input=[doc["page_content"] for doc in docs]
        )
        vectors = [emb.embedding for emb in embeddings.data]
        return cls(docs, vectors, oai_client)

    def query(self, query: str, k: int = 5) -> list[dict]:
        embed = self._client.embeddings.create(model="text-embedding-3-small", input=[query])
        # "@" is just a matrix multiplication in python
        scores = np.array(embed.data[0].embedding) @ self._arr.T
        top_k_idx = np.argpartition(scores, -k)[-k:]
        top_k_idx_sorted = top_k_idx[np.argsort(-scores[top_k_idx])]
        return [{**self._docs[idx], "similarity": scores[idx]} for idx in top_k_idx_sorted]


# Set by init_policies() before building the graph so lookup_policy uses it.
_retriever: VectorStoreRetriever | None = None


def get_retriever(docs):
    logger.info("Getting retriever...")
    return VectorStoreRetriever.from_docs(docs, openai.Client())


def init_policies() -> None:
    """Build retriever from company policies. Call once before building the agent."""
    global _retriever
    docs = get_company_policies()
    _retriever = get_retriever(docs)


@tool
def lookup_policy(query: str) -> str:
    """Consult the company policies to check whether certain options are permitted.
    Use this before making any flight changes performing other 'write' events."""
    if _retriever is None:
        raise RuntimeError("Company policies not initialized; call init_policies() first.")
    logger.info(f"Looking up policy for query: {query}")
    docs = _retriever.query(query, k=2)
    return "\n\n".join([doc["page_content"] for doc in docs])
