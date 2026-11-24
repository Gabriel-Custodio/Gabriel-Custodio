"""Registro de canais permitidos do monitor de lives (só biblioteca padrão).

Regra do projeto: o monitor só assiste a lives de streamers que permitem cortar a live e postar os cortes em outra
plataforma; quem coloca o canal na lista responde por isso (não há campo de confirmação). A permissão é vitalícia: o
canal fica na lista até alguém suspender ou apagar. A lista tem no máximo MAX_CANAIS canais. O monitor só pode
entrar numa live cujo canal esteja ativo. Qualquer dúvida (arquivo ilegível, link que não dá para identificar) conta
como NÃO permitido.

Arquivo: canais_permitidos.json na pasta do projeto (fica fora do git). Formato: {"versao": 1, "canais": [...]}.
"""
import json
import re
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

PLATAFORMAS = ("twitch", "youtube")
NOMES_PLATAFORMA = {"twitch": "Twitch", "youtube": "YouTube"}
MAX_CANAIS = 2                 # a lista aceita no máximo 2 canais (o mesmo limite de lives simultâneas)
RE_TWITCH = re.compile(r"^[a-z0-9_]{3,25}$")
RE_YT_HANDLE = re.compile(r"^@[\w.\-]{3,30}$")
RE_YT_ID = re.compile(r"^UC[\w\-]{22}$")
RE_SLUG = re.compile(r"^[a-z0-9_-]{1,40}$")
HOSTS_TWITCH = {"twitch.tv", "www.twitch.tv", "m.twitch.tv", "player.twitch.tv"}
HOSTS_YOUTUBE = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
# endereços da Twitch que não são canais
TWITCH_RESERVADOS = {"videos", "directory", "settings", "p", "downloads", "jobs", "turbo", "subscriptions", "friends",
                     "wallet", "clips", "popout", "embed", "store", "prime", "login", "signup", "search", "inventory",
                     "drops", "moderator", "u", "team", "broadcast", "dashboard"}


class CanalInvalido(ValueError):
    """Canal ou link que não dá para identificar."""


def _sem_controle(texto):
    return "".join(c for c in str(texto) if ord(c) >= 32 and ord(c) != 127)


def normalizar_canal(plataforma, valor):
    """Forma canônica do canal: Twitch = login em minúsculas; YouTube = @handle em minúsculas ou ID UC… (como veio)."""
    valor = _sem_controle(valor or "").strip()
    if plataforma == "twitch":
        valor = valor.lstrip("@").lower()
        if not RE_TWITCH.match(valor) or valor in TWITCH_RESERVADOS:
            raise CanalInvalido("Nome de canal da Twitch inválido (use o login: letras, números e _).")
        return valor
    if plataforma == "youtube":
        if RE_YT_ID.match(valor):
            return valor
        if not valor.startswith("@"):
            valor = "@" + valor
        valor = valor.lower()
        if not RE_YT_HANDLE.match(valor):
            raise CanalInvalido("Canal do YouTube inválido: use o @nome do canal ou o ID que começa com UC.")
        return valor
    raise CanalInvalido("Plataforma inválida: use Twitch ou YouTube.")


def canal_de_url(texto):
    """(plataforma, canal) a partir de um link. Levanta CanalInvalido se não der para saber de qual canal é."""
    texto = _sem_controle(texto or "").strip()
    if not texto or len(texto) > 2000:
        raise CanalInvalido("Cole o link do canal ou da live.")
    if "://" not in texto:
        texto = "https://" + texto
    try:
        url = urlparse(texto)
    except ValueError:
        raise CanalInvalido("Link inválido.")
    host = (url.hostname or "").lower()
    partes = [unquote(p) for p in url.path.split("/") if p]
    if host in HOSTS_TWITCH:
        if host == "player.twitch.tv":
            login = (parse_qs(url.query).get("channel") or [""])[0]
        else:
            login = partes[0] if partes else ""
        if not login:
            raise CanalInvalido("Não encontrei o canal da Twitch nesse link (use twitch.tv/nome_do_canal).")
        return "twitch", normalizar_canal("twitch", login)
    if host in HOSTS_YOUTUBE:
        if host == "youtu.be" or (partes and partes[0] in ("watch", "live", "shorts", "embed")):
            raise CanalInvalido("Esse link de vídeo do YouTube não diz de qual canal ele é. "
                                "Use o link do canal (youtube.com/@nome).")
        if partes and partes[0].startswith("@"):
            return "youtube", normalizar_canal("youtube", partes[0])
        if len(partes) >= 2 and partes[0] == "channel":
            return "youtube", normalizar_canal("youtube", partes[1])
        raise CanalInvalido("Link do YouTube não reconhecido: use youtube.com/@nome ou youtube.com/channel/UC…")
    raise CanalInvalido("Só aceitamos links da Twitch e do YouTube.")


def identificar(texto, plataforma=None):
    """Aceita link ou nome. Com plataforma informada e um nome simples, normaliza direto."""
    texto = _sem_controle(texto or "").strip()
    parece_link = "/" in texto or (plataforma is None and "." in texto)
    if parece_link:
        return canal_de_url(texto)
    if plataforma in PLATAFORMAS:
        return plataforma, normalizar_canal(plataforma, texto)
    raise CanalInvalido("Informe a plataforma ou cole o link do canal.")


def url_da_live(plataforma, canal):
    """Endereço que o yt-dlp resolve para a live ATUAL do canal (a página do canal ao vivo)."""
    if plataforma == "twitch":
        return f"https://www.twitch.tv/{canal}"
    if plataforma == "youtube":
        return f"https://www.youtube.com/{canal}/live" if canal.startswith("@") else f"https://www.youtube.com/channel/{canal}/live"
    raise CanalInvalido("Plataforma inválida.")


class RegistroCanais:
    """Lê e grava canais_permitidos.json com segurança (escrita atômica, trava, falha para o lado seguro)."""

    def __init__(self, arquivo):
        self.arquivo = Path(arquivo)
        self.trava = threading.Lock()

    # ---- arquivo
    def _ler(self):
        """Lista de canais crus. Arquivo ausente = vazio; arquivo ilegível = vazio (e ninguém é permitido)."""
        try:
            dados = json.loads(self.arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return [], self.arquivo.exists()
        itens = dados.get("canais") if isinstance(dados, dict) else None
        if not isinstance(itens, list):
            return [], True
        return [c for c in itens if isinstance(c, dict) and c.get("id")], False

    def _gravar(self, canais, corrompido):
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        if corrompido and self.arquivo.exists():                       # nunca apaga em silêncio um arquivo ilegível
            self.arquivo.replace(self.arquivo.with_suffix(".corrompido.json"))
        tmp = self.arquivo.with_suffix(".tmp")
        tmp.write_text(json.dumps({"versao": 1, "canais": canais}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.arquivo)

    # ---- situação
    @staticmethod
    def situacao(canal):
        return "ativo" if canal.get("ativo", True) is True else "suspenso"

    def _publico(self, canal):
        saida = {k: v for k, v in canal.items() if k not in ("origem", "data_permissao", "revisar_ate")}
        saida["situacao"] = self.situacao(canal)
        saida["url_live"] = url_da_live(canal.get("plataforma", ""), canal.get("canal", "")) if canal.get("plataforma") in PLATAFORMAS else ""
        return saida

    # ---- operações
    def listar(self):
        with self.trava:
            canais, _ = self._ler()
        itens = [self._publico(c) for c in canais]
        return sorted(itens, key=lambda c: (c["situacao"] != "ativo", c.get("plataforma", ""), c.get("nome", "").lower()))

    def salvar(self, dados):
        """Cadastra ou atualiza um canal. Levanta ValueError com a mensagem para a tela.

        "canal" pode ser o link do canal (a plataforma é descoberta pelo link) ou o nome (aí "plataforma" é obrigatória).
        """
        if not isinstance(dados, dict):
            raise ValueError("Pedido inválido.")
        plataforma = dados.get("plataforma") or None
        if plataforma is not None and plataforma not in PLATAFORMAS:
            raise ValueError("Plataforma inválida: use Twitch ou YouTube.")
        try:
            plat, canal = identificar(dados.get("canal"), plataforma)
        except CanalInvalido as e:
            raise ValueError(str(e))
        if plataforma and plat != plataforma:
            raise ValueError("O link é de outra plataforma. Confira a plataforma escolhida.")
        plataforma = plat
        nome = re.sub(r"\s+", " ", _sem_controle(dados.get("nome") or "")).strip()[:60] or canal.lstrip("@")
        observacoes = re.sub(r"\s+", " ", _sem_controle(dados.get("observacoes") or "")).strip()[:300]
        layout = str(dados.get("layout") or "").strip()
        if layout and not RE_SLUG.match(layout):
            raise ValueError("Layout inválido.")
        ativo = dados.get("ativo", True)
        if not isinstance(ativo, bool):
            raise ValueError("Opção inválida.")

        identificador = f"{plataforma}:{canal}"
        agora = datetime.now().isoformat(timespec="seconds")
        with self.trava:
            canais, corrompido = self._ler()
            antigo = next((c for c in canais if c["id"] == identificador), None)
            if antigo is None and len(canais) >= MAX_CANAIS:
                raise ValueError(f"A lista aceita no máximo {MAX_CANAIS} canais. Apague um canal para adicionar outro.")
            entrada = {
                "id": identificador, "plataforma": plataforma, "canal": canal, "nome": nome,
                "pode_cortar": True, "pode_postar": True, "ativo": ativo, "layout": layout, "observacoes": observacoes,
                "criado_em": antigo.get("criado_em", agora) if antigo else agora, "atualizado_em": agora,
            }
            canais = [c for c in canais if c["id"] != identificador] + [entrada]
            self._gravar(canais, corrompido)
        return self._publico(entrada)

    def definir_ativo(self, identificador, ativo):
        if not isinstance(ativo, bool):
            raise ValueError("Opção inválida.")
        with self.trava:
            canais, corrompido = self._ler()
            alvo = next((c for c in canais if c["id"] == identificador), None)
            if alvo is None:
                return None
            alvo["ativo"] = ativo
            alvo["atualizado_em"] = datetime.now().isoformat(timespec="seconds")
            self._gravar(canais, corrompido)
        return self._publico(alvo)

    def apagar(self, identificador):
        with self.trava:
            canais, corrompido = self._ler()
            restantes = [c for c in canais if c["id"] != identificador]
            if len(restantes) == len(canais):
                return False
            self._gravar(restantes, corrompido)
        return True

    def verificar(self, texto, plataforma=None):
        """Pode o monitor assistir a esta live? -> {"permitido": bool, "motivo": str, ...}. Nunca levanta erro."""
        try:
            plat, canal = identificar(texto, plataforma)
        except CanalInvalido as e:
            return {"permitido": False, "motivo": str(e)}
        with self.trava:
            canais, _ = self._ler()
        achado = next((c for c in canais if c["id"] == f"{plat}:{canal}"), None)
        base = {"plataforma": plat, "canal": canal}
        if achado is None:
            return {**base, "permitido": False, "motivo": f"O canal {canal} ({NOMES_PLATAFORMA[plat]}) não está na lista "
                    "de canais permitidos. Só assistimos a quem permite cortar e postar os cortes em outra plataforma."}
        publico = self._publico(achado)
        if publico["situacao"] == "suspenso":
            return {**base, "permitido": False, "canal_info": publico, "motivo": "Este canal está suspenso na lista."}
        if achado.get("pode_cortar") is not True or achado.get("pode_postar") is not True:
            return {**base, "permitido": False, "canal_info": publico, "motivo": "O cadastro não tem as duas permissões."}
        return {**base, "permitido": True, "canal_info": publico, "motivo": "Canal permitido."}
