-- The title and editors already appear in the body after the official cover.
function Meta(meta)
  meta.title = nil
  meta.author = nil
  return meta
end

function Para(block)
  if #block.content == 1 and block.content[1].tag == "Image"
      and block.content[1].identifier == "official-cover" then
    return {
      block,
      pandoc.RawBlock("openxml",
        '<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
    }
  end
end
