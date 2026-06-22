"""Instancia única + paso de enlaces nxm:// entre procesos.

Cuando Windows abre un nxm:// y ya hay una ventana abierta, este módulo reenvía el
enlace a la instancia existente (en vez de abrir otra ventana).
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

SERVER_NAME = "BMI_SingleInstance"


class SingleInstance(QObject):
    message_received = Signal(str)   # texto recibido de otra instancia (p.ej. un nxm://)

    def __init__(self):
        super().__init__()
        self._server: QLocalServer | None = None
        self.is_primary = False

    def try_acquire(self) -> bool:
        """Intenta ser la instancia primaria. Devuelve True si lo consigue."""
        socket = QLocalSocket()
        socket.connectToServer(SERVER_NAME)
        if socket.waitForConnected(300):
            # Ya hay otra instancia.
            socket.disconnectFromServer()
            self.is_primary = False
            return False

        # Limpia un servidor anterior huérfano y crea el nuestro.
        QLocalServer.removeServer(SERVER_NAME)
        self._server = QLocalServer()
        if not self._server.listen(SERVER_NAME):
            self.is_primary = False
            return False
        self._server.newConnection.connect(self._on_new_connection)
        self.is_primary = True
        return True

    def _on_new_connection(self) -> None:
        if not self._server:
            return
        conn = self._server.nextPendingConnection()
        if conn is None:
            return

        def read():
            data = bytes(conn.readAll()).decode("utf-8", errors="ignore").strip()
            if data:
                self.message_received.emit(data)
            conn.disconnectFromServer()

        conn.readyRead.connect(read)

    @staticmethod
    def send_to_primary(message: str, timeout_ms: int = 1000) -> bool:
        """Envía un mensaje (p.ej. un nxm://) a la instancia primaria."""
        socket = QLocalSocket()
        socket.connectToServer(SERVER_NAME)
        if not socket.waitForConnected(timeout_ms):
            return False
        socket.write(message.encode("utf-8"))
        socket.flush()
        socket.waitForBytesWritten(timeout_ms)
        socket.disconnectFromServer()
        return True
