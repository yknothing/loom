import React from "react";
import { NavLink, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, ListChecks, BookOpen, Search, Network,
  PlusCircle, ShieldAlert, Settings2, LogOut,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";

const NAV = [
  { to: "/", label: "总览", icon: LayoutDashboard, testid: "nav-dashboard" },
  { to: "/tasks", label: "任务队列", icon: ListChecks, testid: "nav-tasks" },
  { to: "/wiki", label: "知识库", icon: BookOpen, testid: "nav-wiki" },
  { to: "/search", label: "全文搜索", icon: Search, testid: "nav-search" },
  { to: "/graph", label: "知识图谱", icon: Network, testid: "nav-graph" },
  { to: "/submit", label: "提交内容", icon: PlusCircle, testid: "nav-submit" },
  { to: "/review", label: "审查队列", icon: ShieldAlert, testid: "nav-review" },
  { to: "/settings", label: "设置", icon: Settings2, testid: "nav-settings" },
];

export default function Layout({ children }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-white">
      <aside className="fixed inset-y-0 left-0 w-60 border-r border-line bg-white flex flex-col z-20">
        <div className="px-6 py-6 border-b border-line">
          <div className="font-mono text-xl font-semibold tracking-tight">
            LOOM<span className="text-primary">_</span>
          </div>
          <div className="label-xs mt-1">知识编译控制台</div>
        </div>
        <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
          {NAV.map(({ to, label, icon: Icon, testid }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              data-testid={testid}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 text-sm font-medium rounded-md transition-colors ${
                  isActive
                    ? "bg-primary text-white"
                    : "text-zinc-600 hover:bg-surface hover:text-ink"
                }`
              }
            >
              <Icon className="w-4 h-4" strokeWidth={1.75} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="px-4 py-4 border-t border-line">
          <div className="text-sm font-medium truncate" data-testid="user-name">
            {user?.name}
          </div>
          <div className="text-xs text-muted truncate">{user?.email}</div>
          <button
            data-testid="logout-btn"
            onClick={async () => {
              await logout();
              navigate("/login");
            }}
            className="mt-3 flex items-center gap-2 text-xs text-zinc-500 hover:text-red-600 transition-colors"
          >
            <LogOut className="w-3.5 h-3.5" /> 退出登录
          </button>
        </div>
      </aside>
      <main className="ml-60 px-10 py-10 max-w-[1400px]">{children}</main>
    </div>
  );
}
