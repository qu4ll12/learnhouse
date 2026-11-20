import { Node, mergeAttributes } from '@tiptap/core'

// Communities are removed. Saved content can still hold "#Community" tags;
// keeping the node (as plain text) stops the editor from dropping them on save.
export const CommunityTag = Node.create({
  name: 'communityTag',
  group: 'inline',
  inline: true,
  atom: true,

  addAttributes() {
    return {
      communityUuid: {
        default: null,
        parseHTML: (el) => el.getAttribute('data-community'),
        renderHTML: (attrs) => ({ 'data-community': attrs.communityUuid }),
      },
      name: {
        default: '',
        parseHTML: (el) => el.getAttribute('data-name') || '',
        renderHTML: (attrs) => ({ 'data-name': attrs.name }),
      },
    }
  },

  parseHTML() {
    return [{ tag: 'community-tag' }]
  },

  renderHTML({ HTMLAttributes, node }) {
    return ['community-tag', mergeAttributes(HTMLAttributes), `#${node.attrs.name}`]
  },

  renderText({ node }) {
    return `#${node.attrs.name}`
  },
})
