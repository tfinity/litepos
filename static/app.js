/* POS System - Client-side utilities */

/**
 * Filter a table by searching across all visible columns.
 * @param {string} inputId - The search input element ID
 * @param {string} tableId - The table element ID
 */
function filterTable(inputId, tableId) {
    const query = document.getElementById(inputId).value.toLowerCase();
    const table = document.getElementById(tableId);
    if (!table) return;
    const rows = table.querySelectorAll('tbody tr');
    rows.forEach(row => {
        const text = row.textContent.toLowerCase();
        row.style.display = text.includes(query) ? '' : 'none';
    });
}
