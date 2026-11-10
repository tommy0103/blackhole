$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$image = [System.Windows.Forms.Clipboard]::GetImage()
if ($null -eq $image) {
    throw 'The Windows clipboard has no image. Take a screenshot or copy an image first.'
}
$buffer = New-Object System.IO.MemoryStream
try {
    # Return data only. Never open a path in the agent-writable project from Windows.
    $image.Save($buffer, [System.Drawing.Imaging.ImageFormat]::Png)
    [Console]::Out.Write([Convert]::ToBase64String($buffer.ToArray()))
} finally {
    $buffer.Dispose()
    $image.Dispose()
}
