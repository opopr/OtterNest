import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthProvider";
import AppShell from "./components/AppShell";
import LoginPage from "./pages/LoginPage";
import OverviewPage from "./pages/OverviewPage";
import ProjectsPage from "./pages/ProjectsPage";
import AgentsPage from "./pages/AgentsPage";
import GovernancePage from "./pages/GovernancePage";
import GoalsWorkflowPage from "./pages/GoalsWorkflowPage";
import KnowledgePage from "./pages/KnowledgePage";
import PortfolioPage from "./pages/PortfolioPage";
import SystemPage from "./pages/SystemPage";
import SkillsPage from "./pages/SkillsPage";
import AccessPage from "./pages/AccessPage";
import ArtifactsPage from "./pages/ArtifactsPage";

function AuthGate() {
  const { checking, isAuthenticated } = useAuth();
  if (checking) {
    return <div className="boot-screen"><div className="boot-mascot">🦦</div><div className="spinner" /><p>正在连接 OtterNest 控制平面…</p></div>;
  }
  if (!isAuthenticated) return <LoginPage />;
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<OverviewPage />} />
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/agents" element={<AgentsPage />} />
          <Route path="/governance" element={<GovernancePage />} />
          <Route path="/goals-workflows" element={<GoalsWorkflowPage />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="/portfolio" element={<PortfolioPage />} />
          <Route path="/system" element={<SystemPage />} />
          <Route path="/skills" element={<SkillsPage />} />
          <Route path="/access" element={<AccessPage />} />
          <Route path="/artifacts" element={<ArtifactsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default function App() {
  return <AuthProvider><AuthGate /></AuthProvider>;
}
