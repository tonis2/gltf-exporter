from __future__ import annotations

from typing import TYPE_CHECKING

from ..gltf.constants import TextureFilter, TextureWrap

if TYPE_CHECKING:
    import bpy
    from ..gltf.types import Gltf, TextureInfo, NormalTextureInfo
    from .texture import TextureImporter
    from ..importer import ImportSettings


class MaterialImporter:
    def __init__(
        self,
        gltf: "Gltf",
        texture_importer: "TextureImporter",
        settings: "ImportSettings",
    ) -> None:
        self.gltf = gltf
        self.texture_importer = texture_importer
        self.settings = settings
        self.blender_materials: dict[int, "bpy.types.Material"] = {}

    def import_all(self) -> None:
        if self.gltf.materials is None:
            return
        for i, gltf_mat in enumerate(self.gltf.materials):
            self.blender_materials[i] = self._import_material(i, gltf_mat)

    def get_blender_material(self, material_index: int) -> "bpy.types.Material | None":
        return self.blender_materials.get(material_index)

    def _import_material(self, index, gltf_mat) -> "bpy.types.Material":
        import bpy

        name = gltf_mat.name or f"Material_{index}"
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        tree = mat.node_tree
        tree.nodes.clear()

        principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
        principled.location = (0, 0)
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        output.location = (400, 0)
        tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])

        pbr = gltf_mat.pbr_metallic_roughness
        if pbr:
            self._apply_pbr(tree, principled, pbr)

        if gltf_mat.normal_texture:
            self._apply_normal_texture(tree, principled, gltf_mat.normal_texture)

        if gltf_mat.emissive_factor:
            r, g, b = gltf_mat.emissive_factor
            strength = max(r, g, b)
            if strength > 0:
                principled.inputs["Emission Color"].default_value = (
                    r / strength, g / strength, b / strength, 1.0,
                )
                principled.inputs["Emission Strength"].default_value = strength

        if gltf_mat.emissive_texture:
            self._apply_texture(tree, principled, "Emission Color", gltf_mat.emissive_texture, y_offset=-400)

        if gltf_mat.alpha_mode == "BLEND":
            if hasattr(mat, "surface_render_method"):
                mat.surface_render_method = "BLENDED"
        elif gltf_mat.alpha_mode == "MASK":
            if hasattr(mat, "surface_render_method"):
                mat.surface_render_method = "DITHERED"
            mat.alpha_threshold = gltf_mat.alpha_cutoff if gltf_mat.alpha_cutoff is not None else 0.5

        if gltf_mat.double_sided:
            mat.use_backface_culling = False
        else:
            mat.use_backface_culling = True

        return mat

    def _apply_pbr(self, tree, principled, pbr) -> None:
        if pbr.base_color_factor:
            principled.inputs["Base Color"].default_value = tuple(pbr.base_color_factor[:4])
            if len(pbr.base_color_factor) > 3:
                principled.inputs["Alpha"].default_value = pbr.base_color_factor[3]

        if pbr.metallic_factor is not None:
            principled.inputs["Metallic"].default_value = pbr.metallic_factor

        if pbr.roughness_factor is not None:
            principled.inputs["Roughness"].default_value = pbr.roughness_factor

        if pbr.base_color_texture:
            self._apply_texture(tree, principled, "Base Color", pbr.base_color_texture, y_offset=0)

        if pbr.metallic_roughness_texture:
            self._apply_texture(tree, principled, "Metallic", pbr.metallic_roughness_texture, y_offset=-200)

    def _apply_texture(self, tree, principled, socket_name, texture_info, y_offset=0) -> None:
        if self.gltf.textures is None:
            return
        tex_index = texture_info.index
        if tex_index >= len(self.gltf.textures):
            return
        gltf_texture = self.gltf.textures[tex_index]
        if gltf_texture.source is None:
            return

        img = self.texture_importer.get_blender_image(gltf_texture.source)
        if img is None:
            return

        tex_node = tree.nodes.new("ShaderNodeTexImage")
        tex_node.image = img
        tex_node.location = (-400, y_offset)

        if gltf_texture.sampler is not None and self.gltf.samplers:
            sampler = self.gltf.samplers[gltf_texture.sampler]
            if sampler.mag_filter == TextureFilter.NEAREST:
                tex_node.interpolation = "Closest"
            else:
                tex_node.interpolation = "Linear"
            if sampler.wrap_s == TextureWrap.CLAMP_TO_EDGE:
                tex_node.extension = "EXTEND"
            else:
                tex_node.extension = "REPEAT"

        tree.links.new(tex_node.outputs["Color"], principled.inputs[socket_name])

    def _apply_normal_texture(self, tree, principled, normal_info) -> None:
        if self.gltf.textures is None:
            return
        tex_index = normal_info.index
        if tex_index >= len(self.gltf.textures):
            return
        gltf_texture = self.gltf.textures[tex_index]
        if gltf_texture.source is None:
            return

        img = self.texture_importer.get_blender_image(gltf_texture.source)
        if img is None:
            return

        tex_node = tree.nodes.new("ShaderNodeTexImage")
        tex_node.image = img
        tex_node.image.colorspace_settings.name = "Non-Color"
        tex_node.location = (-600, -600)

        normal_map = tree.nodes.new("ShaderNodeNormalMap")
        normal_map.location = (-300, -600)
        if normal_info.scale is not None:
            normal_map.inputs["Strength"].default_value = normal_info.scale

        tree.links.new(tex_node.outputs["Color"], normal_map.inputs["Color"])
        tree.links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
