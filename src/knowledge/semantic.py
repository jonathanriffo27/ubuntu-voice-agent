import math
import re
from typing import List, Tuple, Dict
import numpy as np


class LocalSemanticSearch:
    """
    Motor de búsqueda semántica local basado en vectorización n-gram/TF-IDF y similitud coseno.
    Permite encontrar notas y recuerdos aunque no coincidan las palabras exactas,
    con 0 latencia y sin dependencias externas pesadas.
    """

    def __init__(self):
        pass

    def _tokenize(self, text: str) -> List[str]:
        """Extrae palabras y n-gramas de caracteres para capturar similitudes morfológicas."""
        text = text.lower()
        words = re.findall(r'\b\w+\b', text)

        # Agregar trigramas de caracteres para similitud semántica y tolerancia a variaciones
        trigrams = []
        for word in words:
            if len(word) >= 3:
                for i in range(len(word) - 2):
                    trigrams.append(f"${word[i:i+3]}")

        return words + trigrams

    def _build_vector(self, tokens: List[str], vocabulary: Dict[str, int]) -> np.ndarray:
        """Construye un vector ponderado de frecuencias."""
        vec = np.zeros(len(vocabulary), dtype=np.float32)
        for token in tokens:
            if token in vocabulary:
                vec[vocabulary[token]] += 1.0

        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec

    def rank(self, query: str, documents: List[str], top_k: int = 5) -> List[Tuple[int, str, float]]:
        """
        Ordena los documentos por similitud coseno contra la consulta.
        Devuelve lista de (indice, documento, score).
        """
        if not query or not documents:
            return []

        query_tokens = self._tokenize(query)
        doc_tokens_list = [self._tokenize(doc) for doc in documents]

        # Construir vocabulario único
        all_tokens = set(query_tokens)
        for d_toks in doc_tokens_list:
            all_tokens.update(d_toks)

        vocabulary = {tok: idx for idx, tok in enumerate(all_tokens)}

        query_vec = self._build_vector(query_tokens, vocabulary)
        scored_docs = []

        for idx, (doc, doc_tokens) in enumerate(zip(documents, doc_tokens_list)):
            doc_vec = self._build_vector(doc_tokens, vocabulary)
            score = float(np.dot(query_vec, doc_vec))

            # Coincidencia exacta directa de subcadena recibe bonus de relevancia
            if query.lower() in doc.lower():
                score = max(score, 0.85)

            if score > 0.15:  # Umbral mínimo de relevancia
                scored_docs.append((idx, doc, score))

        scored_docs.sort(key=lambda x: x[2], reverse=True)
        return scored_docs[:top_k]
