'use strict';
/* FILES.JS — File explorer, tree, upload, download, context menus */

async function loadFiles() {
    try {
        const res = await fetch('/api/files');
        const data = await res.json();
        NX.allFiles = data.files || [];
        document.getElementById('wsFileCount').textContent = NX.allFiles.length + ' files';
        const badge = document.getElementById('ab-badge-explorer');
        if (badge) badge.textContent = NX.allFiles.length || '';
        renderFileTree(NX.allFiles);
    } catch (e) {
        document.getElementById('fileTree').innerHTML = '<div class="fe-empty-state">Error loading files</div>';
    }
}

function renderFileTree(files) {
    const tree = document.getElementById('fileTree');
    if (!files.length) {
        tree.innerHTML = '<div class="fe-empty-state"><i class="fa-solid fa-folder-open"></i><p>Workspace is empty</p><button onclick="showNewFileDialog()">Create first file</button></div>';
        return;
    }
    tree.innerHTML = '';
    const root = {};
    files.forEach(f => {
        const path = typeof f === 'object' ? f.path : f;
        const size = typeof f === 'object' ? f.size : 0;
        const parts = path.split('/');
        let curr = root;
        parts.forEach((p, i) => {
            if (i === parts.length - 1) curr[p] = { _file: true, _path: path, _size: size };
            else { if (!curr[p]) curr[p] = {}; curr = curr[p]; }
        });
    });
    _renderNode(root, tree, 0);
    updateDiffSelectors(files);
}

function _renderNode(node, container, depth) {
    Object.keys(node).sort((a, b) => {
        const af = node[a]._file, bf = node[b]._file;
        if (af && !bf) return 1; if (!af && bf) return -1;
        return a.localeCompare(b);
    }).forEach(key => {
        if (key.startsWith('_')) return;
        const entry = node[key];
        if (entry._file) {
            const div = document.createElement('div');
            div.className = 'fe-item' + (entry._path === NX.currentFile ? ' active' : '');
            div.style.paddingLeft = (12 + depth * 16) + 'px';
            div.dataset.path = entry._path;
            div.innerHTML = `<i class="${fileIcon(key)}"></i><span class="fe-name">${key}</span>`;
            div.onclick = () => openFileInEditor(entry._path);
            div.oncontextmenu = e => { e.preventDefault(); _ctxFilePath = entry._path; showCtxMenu('fileCtxMenu', e.pageX, e.pageY); };
            container.appendChild(div);
        } else {
            const dirDiv = document.createElement('div');
            dirDiv.className = 'fe-dir';
            dirDiv.style.paddingLeft = (12 + depth * 16) + 'px';
            dirDiv.innerHTML = `<i class="fa-solid fa-chevron-down fe-dir-chevron"></i><i class="fa-solid fa-folder-open fe-dir-icon"></i><span>${key}</span>`;
            const childC = document.createElement('div');
            childC.className = 'fe-dir-children';
            dirDiv.onclick = () => {
                const open = childC.style.display !== 'none';
                childC.style.display = open ? 'none' : '';
                dirDiv.querySelector('.fe-dir-chevron').className = open ? 'fa-solid fa-chevron-right fe-dir-chevron' : 'fa-solid fa-chevron-down fe-dir-chevron';
                dirDiv.querySelector('.fe-dir-icon').className = open ? 'fa-solid fa-folder fe-dir-icon' : 'fa-solid fa-folder-open fe-dir-icon';
            };
            container.appendChild(dirDiv);
            container.appendChild(childC);
            _renderNode(entry, childC, depth + 1);
        }
    });
}

function fileIcon(name) {
    const ext = name.split('.').pop().toLowerCase();
    const m = { html:'fa-brands fa-html5',js:'fa-brands fa-js',jsx:'fa-brands fa-react',ts:'fa-brands fa-js',py:'fa-brands fa-python',css:'fa-brands fa-css3',json:'fa-solid fa-brackets-curly',md:'fa-brands fa-markdown',sh:'fa-solid fa-terminal',rs:'fa-solid fa-gear',go:'fa-brands fa-golang',yml:'fa-solid fa-file-code',yaml:'fa-solid fa-file-code',svg:'fa-solid fa-image',png:'fa-solid fa-image',jpg:'fa-solid fa-image',lock:'fa-solid fa-lock' };
    if (name.toLowerCase() === 'dockerfile') return 'fa-brands fa-docker';
    if (name.toLowerCase() === 'package.json') return 'fa-brands fa-npm';
    return m[ext] || 'fa-solid fa-file';
}

function filterFiles(q) { if (!q) { renderFileTree(NX.allFiles); return; } const lq = q.toLowerCase(); renderFileTree(NX.allFiles.filter(f => (typeof f === 'object' ? f.path : f).toLowerCase().includes(lq))); }
function showNewFileDialog() { showModal('newFileModal'); }
function showNewFolderDialog() { showModal('newFolderModal'); }
function showOpenFolderDialog() { showModal('openFolderModal'); }
function setNewFilePath(p) { const el = document.getElementById('newFilePath'); if (el) el.value = p; }

async function openFolder() {
    const p = document.getElementById('openFolderName')?.value?.trim();
    if (!p) { showToast('Enter a folder path', 'warning'); return; }
    try {
        const res = await fetch('/api/workspace/open', { 
            method: 'POST', 
            headers: { 'Content-Type': 'application/json' }, 
            body: JSON.stringify({ path: p }) 
        });
        const data = await res.json();
        if (data.ok) {
            hideModal('openFolderModal');
            showToast('Opened: ' + p, 'success');
            document.getElementById('workspaceName').textContent = p.split(/[\\/]/).pop() || p;
            await loadFiles();
        } else {
            showToast(data.message || 'Failed to open folder', 'error');
        }
    } catch {
        showToast('Request failed', 'error');
    }
}

async function createNewFile() {
    const p = document.getElementById('newFilePath')?.value?.trim();
    if (!p) { showToast('Enter a file path', 'warning'); return; }
    const tpl = { html:'<!DOCTYPE html>\n<html>\n<head><title>New</title></head>\n<body>\n\n</body>\n</html>',css:'/* Styles */\n',js:"'use strict';\n",py:'def main():\n    pass\n\nif __name__=="__main__":\n    main()\n',json:'{\n}\n',md:'# Title\n',sh:'#!/bin/bash\n' };
    const ext = p.split('.').pop().toLowerCase();
    try {
        await fetch('/api/file/new', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({path:p,content:tpl[ext]||''}) });
        hideModal('newFileModal'); await loadFiles(); openFileInEditor(p);
        showToast('Created: '+p, 'success');
    } catch { showToast('Create failed','error'); }
}

async function createNewFolder() {
    const p = document.getElementById('newFolderPath')?.value?.trim();
    if (!p) { showToast('Enter a folder path','warning'); return; }
    try {
        await fetch('/api/file/new', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({path:p+'/.gitkeep',content:''}) });
        hideModal('newFolderModal'); await loadFiles(); showToast('Created folder: '+p,'success');
    } catch { showToast('Create failed','error'); }
}

function triggerUpload() { document.getElementById('uploadInput')?.click(); }
async function handleFileUpload(files) {
    const fd = new FormData(); for (const f of files) fd.append(f.name, f);
    try { await fetch('/api/upload',{method:'POST',body:fd}); showToast('Files uploaded','success'); await loadFiles(); } catch { showToast('Upload failed','error'); }
}
async function downloadWorkspace() {
    showToast('Exporting ZIP...','info');
    try { const r=await fetch('/api/workspace/download'); const b=await r.blob(); const a=document.createElement('a'); a.href=URL.createObjectURL(b); a.download='agent_workspace.zip'; a.click(); showToast('Exported!','success'); } catch { showToast('Export failed','error'); }
}
function triggerImport() { document.getElementById('workspaceZipInput')?.click(); }
async function importWorkspace(files) {
    if (!files?.[0]) return; const fd=new FormData(); fd.append('file',files[0]);
    try { const r=await fetch('/api/workspace/upload',{method:'POST',body:fd}); const d=await r.json(); showToast(d.message||'Imported','success'); await loadFiles(); } catch { showToast('Import failed','error'); }
}

function onFileDragOver(e) { e.preventDefault(); document.getElementById('feDropOverlay').style.display='flex'; }
function onFileDragLeave(e) { document.getElementById('feDropOverlay').style.display='none'; }
async function onFileDrop(e) { e.preventDefault(); document.getElementById('feDropOverlay').style.display='none'; if (e.dataTransfer.files?.length) await handleFileUpload(e.dataTransfer.files); }
function collapseAllFolders() { document.querySelectorAll('.fe-dir-children').forEach(e=>e.style.display='none'); }

let _ctxFilePath = null, _tabCtxIdx = null;
function showCtxMenu(id, x, y) { hideAllCtxMenus(); const m=document.getElementById(id); if(!m) return; m.style.display='block'; m.style.left=Math.min(x,innerWidth-200)+'px'; m.style.top=Math.min(y,innerHeight-300)+'px'; setTimeout(()=>document.addEventListener('click',hideAllCtxMenus,{once:true}),10); }
function hideAllCtxMenus() { document.querySelectorAll('.ctx-menu').forEach(m=>m.style.display='none'); }

function ctxFileOpen() { if(_ctxFilePath) openFileInEditor(_ctxFilePath); }
function ctxFilePreview() { if(_ctxFilePath) { document.getElementById('browserUrlInput').value='/workspace/'+_ctxFilePath; refreshPreview(); switchRightTab('preview'); } }
function ctxFileOpenSplit() { if(_ctxFilePath) { openFileInEditor(_ctxFilePath); splitEditorHorizontal(); } }
function ctxFileCopyPath() { if(_ctxFilePath) { navigator.clipboard.writeText(_ctxFilePath); showToast('Copied','success'); } }
function ctxFileCopyRelPath() { ctxFileCopyPath(); }
function ctxFileRename() { if(!_ctxFilePath)return; document.getElementById('renameInput').value=_ctxFilePath; window._renameOrig=_ctxFilePath; showModal('renameModal'); }
async function confirmRename() { const np=document.getElementById('renameInput')?.value?.trim(); if(!np||!window._renameOrig)return; try { await fetch('/api/file/rename',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({from:window._renameOrig,to:np})}); hideModal('renameModal'); await loadFiles(); showToast('Renamed','success'); } catch { showToast('Rename failed','error'); } }
async function ctxFileDuplicate() { if(!_ctxFilePath)return; try { const r=await fetch('/api/file/'+_ctxFilePath); const d=await r.json(); const ext=_ctxFilePath.includes('.')?'.'+_ctxFilePath.split('.').pop():''; const np=_ctxFilePath.replace(ext,'')+'_copy'+ext; await fetch('/api/file/new',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:np,content:d.content||''})}); await loadFiles(); showToast('Duplicated','success'); } catch { showToast('Failed','error'); } }
async function ctxFileDelete() { if(!_ctxFilePath)return; const ok=await showConfirm('Delete',`Delete "${_ctxFilePath}"?`); if(!ok)return; try { await fetch('/api/file/'+_ctxFilePath,{method:'DELETE'}); const i=NX.openTabs.findIndex(t=>t.path===_ctxFilePath); if(i>=0)closeTab(i); await loadFiles(); showToast('Deleted','success'); } catch { showToast('Failed','error'); } }

function ctxTabClose() { if(_tabCtxIdx!==null) closeTab(_tabCtxIdx); }
function ctxTabCloseOthers() { if(_tabCtxIdx!==null) { NX.openTabs=[NX.openTabs[_tabCtxIdx]]; NX.activeTab=0; switchToTab(0); renderTabs(); } }
function ctxTabCloseAll() { closeAllTabs(); }
function ctxTabSplit() { if(_tabCtxIdx!==null) { switchToTab(_tabCtxIdx); splitEditorHorizontal(); } }
function ctxTabCopyPath() { if(_tabCtxIdx!==null&&NX.openTabs[_tabCtxIdx]) { navigator.clipboard.writeText(NX.openTabs[_tabCtxIdx].path); showToast('Copied','success'); } }
function ctxTabReveal() { if(_tabCtxIdx!==null) togglePanel('explorer'); }

function updateDiffSelectors(files) {
    const sA=document.getElementById('diffFileA'),sB=document.getElementById('diffFileB');
    if(!sA||!sB) return;
    const opts=files.map(f=>{const p=typeof f==='object'?f.path:f;return `<option value="${p}">${p}</option>`;}).join('');
    sA.innerHTML='<option>Select file A</option>'+opts; sB.innerHTML='<option>Select file B</option>'+opts;
}
