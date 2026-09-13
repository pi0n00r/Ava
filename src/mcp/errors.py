class MCPError(RuntimeError):
    pass


class MCPProtocolError(MCPError):
    pass


class MCPServerExited(MCPError):
    pass

