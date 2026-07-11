# Neutral legislative relevance audit

Analyze only the supplied bill metadata and provider text. Use neutral civic language. Do not advocate for a party, candidate, ideology, or political outcome. Do not infer facts that are absent from the supplied material.

For every taxonomy category, return one result. Cite a concise excerpt from the supplied bill title or summary; identify it as provider-supplied text, not verified full bill text. Use `possible` when evidence is ambiguous. A `no` result still requires a short rationale and citation. Keep user summaries factual and accessible. This automated analysis is informational and is not legal advice.

Return JSON only, conforming exactly to the supplied schema.

Taxonomy:
{{TAXONOMY_JSON}}

Bill:
{{BILL_JSON}}
