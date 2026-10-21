import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import { AdminLayout } from './pages/AdminLayout'
import { AdminMembersPage } from './pages/AdminMembersPage'
import { AdminModelPage } from './pages/AdminModelPage'
import { AdminOverviewPage } from './pages/AdminOverviewPage'
import { AdminVersionsPage } from './pages/AdminVersionsPage'
import { LoginPage } from './pages/LoginPage'
import { SettingsPage } from './pages/SettingsPage'
import { UnderstandingPage } from './pages/UnderstandingPage'
import { UploadPage } from './pages/UploadPage'
import { WorkbenchPage } from './pages/WorkbenchPage'
import { AppLayout } from './renderer/AppLayout'
import { PublicAppLayout } from './renderer/PublicAppLayout'
import { IndexRoute, ViewRoute } from './renderer/routeScreens'

// Shared admin children for the legacy workspace and any generated system.
const adminRoutes = () => (
  <>
    <Route index element={<AdminOverviewPage />} />
    <Route path="model" element={<AdminModelPage />} />
    <Route path="versions" element={<AdminVersionsPage />} />
    <Route path="members" element={<AdminMembersPage />} />
  </>
)

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Workbench: every generated system, plus the entry to a new import. */}
        <Route path="/" element={<WorkbenchPage />} />
        <Route path="/new" element={<UploadPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        {/* Structured browse of the understanding result before Confirm. */}
        <Route path="/understanding" element={<UnderstandingPage />} />
        {/* /app keeps the legacy workspace; every generated system lives at
            /app/:appKey and shares the same renderer shell. */}
        <Route path="/app" element={<AppLayout />}>
          <Route index element={<IndexRoute />} />
          <Route path="views/:viewKey" element={<ViewRoute />} />
        </Route>
        <Route path="/app/demo" element={<AppLayout />}>
          <Route index element={<IndexRoute />} />
          <Route path="views/:viewKey" element={<ViewRoute />} />
        </Route>
        <Route path="/app/admin" element={<AdminLayout />}>
          {adminRoutes()}
        </Route>
        <Route path="/app/:appKey" element={<AppLayout />}>
          <Route index element={<IndexRoute />} />
          <Route path="views/:viewKey" element={<ViewRoute />} />
        </Route>
        <Route path="/app/:appKey/admin" element={<AdminLayout />}>
          {adminRoutes()}
        </Route>
        {/* Published read-only apps at custom public links. */}
        <Route path="/p/:slug" element={<PublicAppLayout />}>
          <Route index element={<IndexRoute />} />
          <Route path="views/:viewKey" element={<ViewRoute />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
