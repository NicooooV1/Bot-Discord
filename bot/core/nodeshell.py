"""Console root sur l'HYPERVISEUR (nœud PVE) via SSH — 2026-07-15.

Pourquoi SSH et pas `termproxy` comme pour les LXC : sur un guest, `termproxy` fait
tourner `pct console <vmid>` côté pvedaemon (root) → n'importe quel compte porteur de
VM.Console obtient un shell root DANS le conteneur. Sur le NŒUD, PVE se comporte
autrement (PVE/API2/Nodes.pm, get_shell_command) :

    if ($user eq 'root@pam') { $cmd = ['/bin/login', '-f', 'root'] }
    else { $cmd = ['/bin/login'] }   # "non-root must always login for now"

Autrement dit seul `root@pam` obtient un shell root direct ; tout autre compte — même
avec Sys.Console — tombe sur un prompt `login:` inutilisable. Et un TOKEN d'API ne s'en
sort pas non plus : l'utilisateur authentifié est alors « root@pam!nom » (HTTPServer.pm),
donc `ne root@pam` → prompt. La seule voie termproxy imposait de stocker le mot de passe
root de PVE en clair dans config.env, ce qui aurait donné, en cas de compromission de
CT106, l'admin COMPLET de l'API PVE (dont la console des guests explicitement exclus).

SSH avec une clé dédiée est strictement moins privilégié et plus révocable :
  - aucun mot de passe stocké ; clé ed25519 dédiée, 0400, lisible du seul `discordbot` ;
  - `restrict,pty,from="10.3.10.106"` dans authorized_keys → inutilisable hors de CT106
    (vérifié : refusée depuis une autre IP source) ;
  - aucun droit accordé sur l'API PVE (pas d'ACL Sys.Console) ;
  - révocation = supprimer la ligne d'authorized_keys (effet immédiat) ;
  - chaque ouverture est tracée par sshd côté HÔTE (« Accepted publickey … from
    10.3.10.106 »), un journal que le bot compromis ne peut pas réécrire.
  - la host key de l'hôte est ÉPINGLÉE (known_hosts dédié, StrictHostKeyChecking) →
    pas de MITM possible depuis le réseau mgmt.

L'interface publique est calquée sur `PveConsole` (alive / read_chunk / send_raw /
force_redraw / close) pour que `TerminalSession` et `TerminalView` fonctionnent tels
quels, sans branche « nœud » dans le rendu.
"""
import asyncio
import logging

log = logging.getLogger("discord-bot.nodeshell")


async def run_readonly(cfg, commande, timeout=25):
    """Exécute UNE commande sur l'hyperviseur et renvoie sa sortie standard.

    Même clé, même host key épinglée et même compte que la console du nœud : cette
    fonction n'ouvre AUCUN privilège nouveau (la clé donne déjà un shell root). Elle
    existe pour que les boucles de fond n'aient pas à instancier une session
    interactive — et pour qu'un appelant ne puisse pas, par construction, laisser une
    connexion ouverte : elle est refermée par le `async with`.

    ⚠️ Réservée à des lectures. Rien ici ne le CONTRAINT techniquement (c'est root au
    bout), mais tout appel qui écrirait doit passer par un chemin audité, pas par ce
    raccourci de supervision."""
    import asyncssh
    async with asyncssh.connect(
            cfg.node_ssh_host, port=cfg.node_ssh_port, username=cfg.node_ssh_user,
            client_keys=[cfg.node_ssh_key], known_hosts=cfg.node_ssh_known_hosts,
            connect_timeout=timeout) as conn:
        r = await asyncio.wait_for(conn.run(commande, check=False), timeout=timeout)
        return r.stdout or ""


class NodeShell:
    """Shell root sur le nœud PVE via SSH. Même contrat que PveConsole."""

    def __init__(self, cfg, cols=80, rows=24):
        self.cfg = cfg
        self.cols = cols
        self.rows = rows
        self.conn = None
        self.proc = None
        self._eof = False

    async def open(self):
        import asyncssh
        # known_hosts épinglé : un fichier absent/illisible ferait tomber asyncssh sur
        # ~/.ssh/known_hosts (inexistant sous ProtectHome=yes) → échec silencieux. On
        # exige donc explicitement le fichier, et JAMAIS known_hosts=None (= pas de
        # vérification du serveur, donc MITM possible sur le réseau mgmt).
        self.conn = await asyncio.wait_for(asyncssh.connect(
            self.cfg.node_ssh_host,
            port=self.cfg.node_ssh_port,
            username=self.cfg.node_ssh_user,
            client_keys=[self.cfg.node_ssh_key],
            known_hosts=self.cfg.node_ssh_known_hosts,
            passphrase=None,
        ), timeout=20)
        # pas de commande => shell de login interactif ; term_type force l'allocation
        # d'un PTY (l'option `pty` d'authorized_keys la ré-autorise malgré `restrict`).
        self.proc = await self.conn.create_process(
            term_type="xterm-256color", term_size=(self.cols, self.rows), encoding="utf-8")

    async def send_raw(self, data: str):
        if not self.alive:
            raise RuntimeError("session fermée")
        self.proc.stdin.write(data)

    async def force_redraw(self):
        """Toggle de taille (SIGWINCH) → redraw complet, comme la console LXC."""
        if not self.alive:
            return
        try:
            self.proc.change_terminal_size(self.cols + 1, self.rows)
            await asyncio.sleep(0.25)
            self.proc.change_terminal_size(self.cols, self.rows)
        except Exception:
            pass

    async def read_chunk(self, timeout=20):
        if self.proc is None:
            return ""
        try:
            data = await asyncio.wait_for(self.proc.stdout.read(4096), timeout=timeout)
        except asyncio.TimeoutError:
            return ""                    # simple inactivité : la session reste vivante
        except Exception:
            self._eof = True             # lien coupé
            return ""
        if data == "":
            # EOF (shell terminé / lien coupé). SANS ce marquage, `alive` resterait vrai
            # et le _reader de TerminalSession tournerait en boucle chaude sur un flux
            # clos — 100 % de CPU jusqu'au timeout d'inactivité.
            self._eof = True
            return ""
        return data

    @property
    def alive(self):
        if self.conn is None or self.proc is None or self._eof:
            return False
        # exit_status vaut None tant que le shell tourne ; is_closing() couvre la coupure
        # du lien sans EOF encore lu.
        try:
            return self.proc.exit_status is None and not self.proc.is_closing()
        except Exception:
            return False

    async def close(self):
        self._eof = True
        try:
            if self.conn is not None:
                self.conn.close()
                await asyncio.shield(self.conn.wait_closed())
        except Exception:
            pass
        finally:
            self.proc = None
            self.conn = None
