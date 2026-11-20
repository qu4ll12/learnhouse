'use client'
import React from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useOrg } from '@components/Contexts/OrgContext'
import { EditorPage } from '@components/Dashboard/Pages/Course/courseEditorUi'
import { getPathThumbnailMediaDirectory } from '@services/media/media'
import type { Path } from '@services/paths/paths'
import { queryKeys } from '@/lib/query/keys'
import { ResourceSeoEditor } from './ResourceSeoEditor'

/** Path editor → SEO tab. */
export function PathSeoTab({ path, orgslug }: { path: Path; orgslug: string }) {
  const org = useOrg() as any
  const queryClient = useQueryClient()
  return (
    <EditorPage width="wide">
      <ResourceSeoEditor
        type="path"
        uuid={path.path_uuid}
        orgslug={orgslug}
        body={path.about}
        isPublic={path.public && path.published}
        thumbnailUrl={path.thumbnail_image ? getPathThumbnailMediaDirectory(org?.org_uuid, path.path_uuid, path.thumbnail_image) : null}
        onSaved={() => queryClient.invalidateQueries({ queryKey: queryKeys.paths.detail(path.path_uuid) })}
      />
    </EditorPage>
  )
}
