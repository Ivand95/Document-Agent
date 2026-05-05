import { useState, useRef, useEffect, useLayoutEffect, useMemo } from "react";
import "./style.css";
import {
  ArrowDownward,
  AutoAwesome,
  Check,
  Description,
  GraphicEq,
  KeyboardArrowDown,
  Logout,
  Send,
} from "@mui/icons-material";
import { Avatar, Button, CircularProgress, IconButton } from "@mui/material";
import { useNavigate } from "react-router";
import { documentAgentWebSocket, audioAgentWebSocket } from "../utils/axios";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

type MessageRole = "user" | "assistant";

interface Message {
  id: string;
  role: MessageRole;
  content: {
    answer: string;
    departament_references: string;
    document_reference: string;
    section_reference: string;
    tags: string;
  };
  timestamp: Date;
}

interface UserInfo {
  access_token: string;
  name: string;
  email: string;
  department: string;
}

type AgentId = "document" | "audio";

interface AgentDef {
  id: AgentId;
  name: string;
  title: string;
  subtitle: string;
  description: string;
  hint: string;
  suggestions: string[];
  icon: React.ReactNode;
  connect: (token: string) => WebSocket;
}

const AGENTS: AgentDef[] = [
  {
    id: "document",
    name: "Document Agent",
    title: "Document Agent",
    subtitle: "Asistente inteligente de documentación",
    description: "Consulta y analiza tus documentos",
    hint: "Document Agent puede cometer errores. Verifica la información importante.",
    suggestions: [
      "Plan estratégico de la cooperativa",
      "Funciones del equipo de trabajo",
      "Valores de la cooperativa",
    ],
    icon: <Description sx={{ fontSize: 14 }} />,
    connect: documentAgentWebSocket,
  },
  {
    id: "audio",
    name: "Audio Agent",
    title: "Audio Agent",
    subtitle: "Procesa y consulta transcripciones de audio",
    description: "Trabaja con grabaciones y transcripciones",
    hint: "Audio Agent puede cometer errores. Verifica la información importante.",
    suggestions: [
      "Resumen de la última reunión",
      "¿Qué decisiones se tomaron?",
      "Temas pendientes de seguimiento",
    ],
    icon: <GraphicEq sx={{ fontSize: 14 }} />,
    connect: audioAgentWebSocket,
  },
];

// ---- UserMenu ----

const UserMenu = ({
  userInfo,
  onLogout,
}: {
  userInfo: UserInfo;
  onLogout: () => void;
}) => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node))
        setOpen(false);
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  return (
    <div className="chat-header-user" ref={ref}>
      <IconButton
        size="small"
        aria-label="Abrir menú de usuario"
        onClick={() => setOpen((v) => !v)}
      >
        <Avatar sx={{ fontSize: 28, color: "var(--chat-text)" }} />
      </IconButton>
      {open && (
        <div className="chat-header-user-info">
          <div className="chat-header-user-info-header">
            <Avatar
              sx={{
                width: 40,
                height: 40,
                fontSize: "1rem",
                fontWeight: 600,
                bgcolor: "var(--chat-accent)",
                color: "var(--chat-text)",
              }}
            >
              {userInfo.name?.charAt(0).toUpperCase()}
            </Avatar>
            <div className="chat-header-user-info-content">
              <p className="chat-header-user-info-name">{userInfo.name}</p>
              <p className="chat-header-user-info-email">{userInfo.email}</p>
              <p className="chat-header-user-info-department">
                {userInfo.department}
              </p>
            </div>
          </div>
          <div className="chat-header-user-info-footer">
            <Button
              onClick={onLogout}
              size="small"
              aria-label="Cerrar sesión"
              variant="text"
              fullWidth
              sx={{
                justifyContent: "flex-start",
                gap: 1,
                color: "var(--chat-text-muted)",
                "&:hover": {
                  bgcolor: "rgba(255,255,255,0.06)",
                  color: "var(--chat-text)",
                },
              }}
              startIcon={<Logout sx={{ fontSize: 18 }} />}
            >
              Cerrar sesión
            </Button>
          </div>
        </div>
      )}
    </div>
  );
};

// ---- WelcomeScreen ----

const WelcomeScreen = ({
  agent,
  onSuggestion,
}: {
  agent: AgentDef;
  onSuggestion: (text: string) => void;
}) => (
  <div className="chat-welcome">
    <div className="chat-welcome-icon">
      <AutoAwesome sx={{ fontSize: 28 }} />
    </div>
    <h2>¿En qué puedo ayudarte?</h2>
    <p>{agent.description}</p>
    <div className="chat-welcome-suggestions">
      {agent.suggestions.map((s) => (
        <button
          key={s}
          type="button"
          className="suggestion-chip"
          onClick={() => onSuggestion(s)}
        >
          {s}
        </button>
      ))}
    </div>
  </div>
);

// ---- AgentSelector ----

const AgentSelector = ({
  agents,
  currentAgentId,
  wsConnected,
  onSelect,
}: {
  agents: AgentDef[];
  currentAgentId: AgentId;
  wsConnected: boolean;
  onSelect: (id: AgentId) => void;
}) => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const current = agents.find((a) => a.id === currentAgentId) ?? agents[0];

  useEffect(() => {
    if (!open) return;
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node))
        setOpen(false);
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  const handleSelect = (id: AgentId) => {
    setOpen(false);
    onSelect(id);
  };

  return (
    <div
      className={`agent-selector ${open ? "agent-selector--open" : ""}`}
      ref={ref}
    >
      <button
        type="button"
        className="agent-selector-trigger"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="Seleccionar agente"
      >
        <span className="agent-selector-icon" aria-hidden>
          {current.icon}
        </span>
        <span className="agent-selector-text">
          <span className="agent-selector-name">{current.name}</span>
          <span
            className={`agent-selector-status ${
              wsConnected
                ? "agent-selector-status--online"
                : "agent-selector-status--offline"
            }`}
          >
            <span className="agent-selector-dot" aria-hidden />
            {wsConnected ? "En línea" : "Conectando…"}
          </span>
        </span>
        <KeyboardArrowDown
          className={`agent-selector-chevron ${open ? "agent-selector-chevron--open" : ""}`}
          sx={{ fontSize: 20 }}
        />
      </button>

      {open && (
        <ul className="agent-selector-menu" role="listbox">
          {agents.map((a) => {
            const selected = a.id === currentAgentId;
            return (
              <li
                key={a.id}
                role="option"
                aria-selected={selected}
                className={`agent-selector-option ${selected ? "agent-selector-option--selected" : ""}`}
                onClick={() => handleSelect(a.id)}
              >
                <span className="agent-selector-option-icon" aria-hidden>
                  {a.icon}
                </span>
                <span className="agent-selector-option-text">
                  <span className="agent-selector-option-name">{a.name}</span>
                  <span className="agent-selector-option-desc">
                    {a.description}
                  </span>
                </span>
                {selected && (
                  <Check
                    className="agent-selector-option-check"
                    sx={{ fontSize: 18 }}
                  />
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
};

// ---- MessageBubble ----

const MessageBubble = ({ msg }: { msg: Message }) => (
  <div
    className={`chat-message chat-message--${msg.role}`}
    role="article"
    aria-label={msg.role === "user" ? "Tu mensaje" : "Respuesta del agente"}
  >
    {msg.role === "assistant" && (
      <div className="chat-message-avatar" aria-hidden>
        <AutoAwesome sx={{ fontSize: 28 }} />
      </div>
    )}
    <div className="chat-message-bubble">
      <div className="chat-message-content">
        <Markdown remarkPlugins={[remarkGfm]}>{msg.content.answer}</Markdown>
      </div>
      <time
        className="chat-message-time"
        dateTime={msg.timestamp.toISOString()}
      >
        {msg.timestamp.toLocaleTimeString("es-ES", {
          hour: "2-digit",
          minute: "2-digit",
        })}
      </time>
    </div>
  </div>
);

// ---- Chat ----

export const Chat = (props: {
  userInfo: UserInfo;
  setUserInfo: (userInfo: UserInfo) => void;
}) => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [showScrollDown, setShowScrollDown] = useState(false);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [thinking, setThinking] = useState(false);
  const navigate = useNavigate();
  const [wsConnected, setWsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const [agentId, setAgentId] = useState<AgentId>("document");

  const currentAgent = useMemo(
    () => AGENTS.find((a) => a.id === agentId) ?? AGENTS[0],
    [agentId],
  );

  useEffect(() => {
    const agent = AGENTS.find((a) => a.id === agentId) ?? AGENTS[0];
    const ws = agent.connect(props.userInfo.access_token);
    wsRef.current = ws;
    setWsConnected(false);

    ws.onopen = () => setWsConnected(true);
    ws.onclose = () => setWsConnected(false);
    ws.onerror = (error: Event) => {
      console.error("WebSocket error", error);
      setWsConnected(false);
    };
    ws.onmessage = (event: MessageEvent) => {
      const data = JSON.parse(event.data);
      if (data.type === "answer" || data.type === "error") {
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            content: data.content,
            timestamp: new Date(),
          },
        ]);
        setThinking(false);
      }
    };

    return () => {
      ws.onopen = null;
      ws.onclose = null;
      ws.onerror = null;
      ws.onmessage = null;
      if (
        ws.readyState === WebSocket.OPEN ||
        ws.readyState === WebSocket.CONNECTING
      ) {
        ws.close();
      }
      if (wsRef.current === ws) wsRef.current = null;
    };
  }, [agentId, props.userInfo.access_token]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    setShowScrollDown(false);
  }, [messages]);

  useLayoutEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.max(textareaRef.current.scrollHeight, 16)}px`;
    }
  }, [input]);

  const handleSelectAgent = (next: AgentId) => {
    if (next === agentId) return;
    setMessages([]);
    setThinking(false);
    setAgentId(next);
  };

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    setShowScrollDown(false);
  };

  const handleMessagesScroll = () => {
    const el = messagesContainerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= 8;
    setShowScrollDown(!atBottom);
  };

  const submitMessage = () => {
    const trimmed = input.trim();
    if (!trimmed) return;
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

    setMessages((prev) => [
      ...prev,
      {
        id: crypto.randomUUID(),
        role: "user",
        content: {
          answer: trimmed,
          departament_references: "",
          document_reference: "",
          section_reference: "",
          tags: "",
        },
        timestamp: new Date(),
      },
    ]);
    wsRef.current.send(trimmed);
    setInput("");
    textareaRef.current?.focus();
    setThinking(true);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !thinking) {
      e.preventDefault();
      submitMessage();
    }
  };

  const handleLogout = () => {
    navigate("/", { replace: true });
    props.setUserInfo({
      access_token: "",
      name: "",
      email: "",
      department: "",
    });
  };

  return (
    <div className="chat-dashboard">
      <header className="chat-header">
        <div className="chat-header-inner">
          <div className="chat-header-icon">
            <AutoAwesome sx={{ fontSize: 28 }} />
          </div>
          <div>
            <h1 className="chat-title">{currentAgent.title}</h1>
            <p className="chat-subtitle">{currentAgent.subtitle}</p>
          </div>
        </div>
        <UserMenu userInfo={props.userInfo} onLogout={handleLogout} />
      </header>

      <main className="chat-main">
        <div
          ref={messagesContainerRef}
          className="chat-messages"
          onScroll={handleMessagesScroll}
        >
          {messages.length === 0 ? (
            <WelcomeScreen agent={currentAgent} onSuggestion={setInput} />
          ) : (
            <>
              {messages.map((msg) => (
                <MessageBubble key={msg.id} msg={msg} />
              ))}

              {thinking && (
                <div
                  className="chat-message chat-message--assistant"
                  role="status"
                  aria-label="El agente está pensando"
                >
                  <div className="chat-message-avatar" aria-hidden>
                    <AutoAwesome sx={{ fontSize: 28 }} />
                  </div>
                  <div className="chat-message-bubble chat-message-bubble--thinking">
                    <CircularProgress
                      size={18}
                      sx={{ color: "var(--chat-text)" }}
                    />
                    <span className="chat-message-thinking-text">
                      Analizando…
                    </span>
                  </div>
                </div>
              )}

              <div
                ref={messagesEndRef}
                className="chat-messages-anchor"
                aria-hidden
              />
            </>
          )}
        </div>

        {showScrollDown && messages.length > 0 && (
          <IconButton
            className="chat-arrow-down"
            color="inherit"
            onClick={scrollToBottom}
            size="small"
          >
            <ArrowDownward fontSize="small" />
          </IconButton>
        )}

        <form
          className="chat-input-wrap"
          onSubmit={(e) => {
            e.preventDefault();
            submitMessage();
          }}
        >
          <div className="chat-input-inner">
            <textarea
              ref={textareaRef}
              className="chat-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Escribe tu mensaje… (Enter para enviar, Shift+Enter para nueva línea)"
              aria-label="Mensaje"
              style={{ minHeight: "16px", resize: "none" }}
            />
            <div className="options-container">
              <AgentSelector
                agents={AGENTS}
                currentAgentId={agentId}
                wsConnected={wsConnected}
                onSelect={handleSelectAgent}
              />
              <button
                type="submit"
                className="chat-send"
                disabled={!input.trim() || thinking || !wsConnected}
                aria-label="Enviar mensaje"
              >
                {thinking ? (
                  <CircularProgress
                    size={20}
                    sx={{ color: "var(--chat-text)" }}
                  />
                ) : (
                  <Send sx={{ fontSize: 20 }} />
                )}
              </button>
            </div>
          </div>
          <p className="chat-input-hint">{currentAgent.hint}</p>
        </form>
      </main>
    </div>
  );
};
