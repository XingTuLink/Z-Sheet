import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import { UnderstandingPage } from './pages/UnderstandingPage'
import { UploadPage } from './pages/UploadPage'
import { AppLayout } from './renderer/AppLayout'
import { PublicAppLayout } from './renderer/PublicAppLayout'
import { IndexRoute, ViewRoute } from './renderer/routeScreens'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Day 12: the first screen is upload + understanding progress. */}
        <Route path="/" element={<UploadPage />} />
        {/* Day 13: structured browse of the understanding result. */}
        <Route path="/understanding" element={<UnderstandingPage />} />
        {/* Day 15: /app is the user's confirmed workspace; /app/demo keeps
            the bundled demo runtime independently browsable. */}
        <Route path="/app" element={<AppLayout />}>
          <Route index element={<IndexRoute />} />
          <Route path="views/:viewKey" element={<ViewRoute />} />
        </Route>
        <Route path="/app/demo" element={<AppLayout />}>
          <Route index element={<IndexRoute />} />
          <Route path="views/:viewKey" element={<ViewRoute />} />
        </Route>
        {/* Day 33: published read-only apps at custom public links. */}
        <Route path="/p/:slug" element={<PublicAppLayout />}>
          <Route index element={<IndexRoute />} />
          <Route path="views/:viewKey" element={<ViewRoute />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
