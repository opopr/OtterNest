import { ProjectSelectionProvider } from "./components/ProjectSelection";
import { lazy } from "react";
import { createBrowserRouter, createRoutesFromElements, RouterProvider, Navigate, Route } from "react-router-dom";
import { DraftNavigationProvider } from "./components/DraftGuard";
import { AuthProvider, useAuth } from "./auth/AuthProvider";
import { SystemModeProvider } from "./auth/SystemModeProvider";
import { DialogProvider } from "./components/AppDialog";
import AppShell from "./components/AppShell";
import BrandMark from "./components/BrandMark";
import LoginPage from "./pages/LoginPage";

const OverviewPage = lazy(() => import("./pages/OverviewPage"));
const ProjectsPage = lazy(() => import("./pages/ProjectsPage"));
const AgentsPage = lazy(() => import("./pages/AgentsPage"));
const RuntimeMonitorPage = lazy(() => import("./pages/RuntimeMonitorPage"));
const TaskCenterPage = lazy(() => import("./pages/TaskCenterPage"));
const CommandCenterPage = lazy(() => import("./pages/CommandCenterPage"));
const GovernancePage = lazy(() => import("./pages/GovernancePage"));
const GoalsWorkflowPage = lazy(() => import("./pages/GoalsWorkflowPage"));
const KnowledgePage = lazy(() => import("./pages/KnowledgePage"));
const PortfolioPage = lazy(() => import("./pages/PortfolioPage"));
const SystemPage = lazy(() => import("./pages/SystemPage"));
const SkillsPage = lazy(() => import("./pages/SkillsPage"));
const AccessPage = lazy(() => import("./pages/AccessPage"));
const ArtifactsPage = lazy(() => import("./pages/ArtifactsPage"));
const ExternalInterfacesPage = lazy(() => import("./pages/ExternalInterfacesPage"));
const AccountSecurityPage = lazy(() => import("./pages/AccountSecurityPage"));
const ProjectPortabilityPage = lazy(() => import("./pages/ProjectPortabilityPage"));

let router: ReturnType<typeof createBrowserRouter> | undefined;
function getRouter() {
  return router ??= createBrowserRouter(createRoutesFromElements(
    <Route element={<ProjectSelectionProvider><DraftNavigationProvider><AppShell /></DraftNavigationProvider></ProjectSelectionProvider>}>
              <Route index element={<OverviewPage />} />
              <Route path="/projects" element={<ProjectsPage />} />
              <Route path="/tasks" element={<TaskCenterPage />} />
              <Route path="/command" element={<CommandCenterPage />} />
              <Route path="/agents" element={<AgentsPage />} />
              <Route path="/interfaces" element={<ExternalInterfacesPage />} />
              <Route path="/account-security" element={<AccountSecurityPage />} />
              <Route path="/project-portability" element={<ProjectPortabilityPage />} />
              <Route path="/runtimes" element={<RuntimeMonitorPage />} />
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
  ), {future:{v7_relativeSplatPath:true}});
}

function AuthGate() {
  const { checking, isAuthenticated } = useAuth();
  if (checking) {
    return (
      <div className="boot-screen">
        <div className="boot-mascot"><BrandMark /></div>
        <div className="spinner" />
        <p>正在连接服务…</p>
      </div>
    );
  }
  if (!isAuthenticated) return <LoginPage />;
  return (
    <SystemModeProvider>
      <DialogProvider>
        <RouterProvider router={getRouter()} future={{ v7_startTransition: true }} />
      </DialogProvider>
    </SystemModeProvider>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AuthGate />
    </AuthProvider>
  );
}
