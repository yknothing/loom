import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { useAuth } from "../context/AuthContext";
import { errText } from "../lib/api";
import { Button, Input } from "../components/ui";

const BG_URL =
  "https://images.unsplash.com/photo-1622547748225-3fc4abd2cca0?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjA1NTZ8MHwxfHNlYXJjaHwxfHxhYnN0cmFjdCUyMG1pbmltYWwlMjBnZW9tZXRyaWN8ZW58MHx8fHwxNzgxMTU5NjE1fDA&ixlib=rb-4.1.0&q=85";

export default function Login() {
  const { login, register } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (mode === "login") await login(email, password);
      else await register(email, password, name);
      toast.success(mode === "login" ? "登录成功" : "注册成功");
      navigate("/");
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen grid lg:grid-cols-2">
      <div className="flex items-center justify-center px-8">
        <div className="w-full max-w-sm">
          <div className="font-mono text-2xl font-semibold tracking-tight mb-1">
            LOOM<span className="text-primary">_</span>
          </div>
          <div className="label-xs mb-10">LLM 知识编译 PIPELINE</div>
          <h1 className="text-3xl font-bold tracking-tight mb-2">
            {mode === "login" ? "登录控制台" : "创建账户"}
          </h1>
          <p className="text-sm text-muted mb-8">
            {mode === "login" ? "使用团队账户访问知识编译控制台" : "加入团队，开始管理知识库"}
          </p>
          <form onSubmit={submit} className="space-y-4">
            {mode === "register" && (
              <div>
                <label className="label-xs block mb-1.5">姓名</label>
                <Input
                  data-testid="register-name-input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="你的名字"
                  required
                />
              </div>
            )}
            <div>
              <label className="label-xs block mb-1.5">邮箱</label>
              <Input
                data-testid="login-email-input"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@team.dev"
                required
              />
            </div>
            <div>
              <label className="label-xs block mb-1.5">密码</label>
              <Input
                data-testid="login-password-input"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                required
              />
            </div>
            {error && (
              <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2" data-testid="login-error">
                {error}
              </div>
            )}
            <Button type="submit" className="w-full" disabled={busy} data-testid="login-submit-btn">
              {busy ? "请稍候…" : mode === "login" ? "登录" : "注册"}
            </Button>
          </form>
          <button
            data-testid="toggle-auth-mode-btn"
            onClick={() => {
              setMode(mode === "login" ? "register" : "login");
              setError("");
            }}
            className="mt-6 text-sm text-primary hover:underline"
          >
            {mode === "login" ? "没有账户？注册新账户" : "已有账户？直接登录"}
          </button>
        </div>
      </div>
      <div
        className="hidden lg:block bg-cover bg-center border-l border-line"
        style={{ backgroundImage: `url(${BG_URL})` }}
      />
    </div>
  );
}
