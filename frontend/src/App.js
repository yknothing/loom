import React from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Toaster } from "sonner";
import { AuthProvider, useAuth } from "./context/AuthContext";
import Layout from "./components/Layout";
import { Spinner } from "./components/ui";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Tasks from "./pages/Tasks";
import Wiki from "./pages/Wiki";
import SearchPage from "./pages/SearchPage";
import GraphPage from "./pages/GraphPage";
import Submit from "./pages/Submit";
import Review from "./pages/Review";
import Settings from "./pages/Settings";

function Protected({ children }) {
  const { user } = useAuth();
  if (user === null)
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Spinner className="w-8 h-8" />
      </div>
    );
  if (user === false) return <Navigate to="/login" replace />;
  return <Layout>{children}</Layout>;
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Toaster position="top-right" richColors />
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<Protected><Dashboard /></Protected>} />
          <Route path="/tasks" element={<Protected><Tasks /></Protected>} />
          <Route path="/wiki" element={<Protected><Wiki /></Protected>} />
          <Route path="/search" element={<Protected><SearchPage /></Protected>} />
          <Route path="/graph" element={<Protected><GraphPage /></Protected>} />
          <Route path="/submit" element={<Protected><Submit /></Protected>} />
          <Route path="/review" element={<Protected><Review /></Protected>} />
          <Route path="/settings" element={<Protected><Settings /></Protected>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
