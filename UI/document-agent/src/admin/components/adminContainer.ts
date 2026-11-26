/** Where MUI portals (dialogs, menus) mount: inside the admin page, so they inherit its theme variables
 * and only the page's own content is hidden from screen readers while they are open. */
export const adminContainer = () => document.querySelector<HTMLElement>(".admin-page");
