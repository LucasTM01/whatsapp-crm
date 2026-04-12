VARIABLES = ["{nome}", "{nome_completo}", "{empresa}", "{ticker}"]


def render(template: str, client: dict) -> str:
    """Substitute variables in template for a given client dict.

    Uses plain str.replace() — intentionally avoids string.Template to prevent
    escaping issues with financial text containing braces (e.g. "{+5.2%}").
    """
    nome_completo = client.get("nome") or ""
    nome = nome_completo.split()[0] if nome_completo.strip() else ""
    empresa = client.get("empresa") or ""
    tickers = client.get("tickers") or ""
    ticker = tickers.split(",")[0].strip() if tickers.strip() else ""

    result = template
    result = result.replace("{nome}", nome)
    result = result.replace("{nome_completo}", nome_completo)
    result = result.replace("{empresa}", empresa)
    result = result.replace("{ticker}", ticker)
    return result


def get_preview(template: str, clients: list[dict]) -> str:
    """Return a rendered preview using the first client in the list."""
    if not clients:
        return template
    return render(template, clients[0])
